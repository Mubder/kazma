"""Document parser plugins, capability records, and runtime readiness."""

from __future__ import annotations

import importlib
import importlib.metadata
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .errors import DocumentFormatError, DocumentUnavailableError

if TYPE_CHECKING:
    from .models import DocumentIR
    from .parsers.common import ParseContext

__all__ = [
    "ParserCapability",
    "ParserPlugin",
    "ParserReadiness",
    "ParserRegistry",
    "get_parser_registry",
]


class ParserReadiness(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class DocumentParser(Protocol):
    """Contract implemented by every in-process parser plugin."""

    def parse(self, path: Path, context: ParseContext) -> DocumentIR: ...


HealthProbe = Callable[[], tuple[ParserReadiness, str | None]]


@dataclass(frozen=True, slots=True)
class ParserPlugin:
    """Static parser registration plus its health probe."""

    parser_id: str
    parser_version: str
    mime_types: tuple[str, ...]
    extensions: tuple[str, ...]
    features: tuple[str, ...]
    limits: tuple[str, ...]
    isolation_required: bool
    factory: Callable[[], DocumentParser]
    dependencies: tuple[str, ...] = ()
    system_binaries: tuple[str, ...] = ()
    health_probe: HealthProbe | None = None
    degraded_features: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ParserCapability:
    parser_id: str
    parser_version: str
    mime_types: tuple[str, ...]
    extensions: tuple[str, ...]
    features: tuple[str, ...]
    limits: tuple[str, ...]
    isolation_required: bool
    readiness: ParserReadiness
    reason: str | None
    dependencies: Mapping[str, str | None]
    system_binaries: Mapping[str, str | None]

    @property
    def available(self) -> bool:
        return self.readiness is not ParserReadiness.UNAVAILABLE


def _dependency_versions(names: tuple[str, ...]) -> tuple[dict[str, str | None], str | None]:
    versions: dict[str, str | None] = {}
    for name in names:
        module_name, _, distribution = name.partition(":")
        try:
            importlib.import_module(module_name)
            package = distribution or module_name.split(".", 1)[0]
            try:
                versions[module_name] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[module_name] = "present"
        except Exception as exc:
            versions[module_name] = None
            return versions, f"dependency {module_name} failed its health probe ({type(exc).__name__})"
    return versions, None


def _binary_versions(names: tuple[str, ...]) -> tuple[dict[str, str | None], str | None]:
    versions: dict[str, str | None] = {}
    for name in names:
        # Accept absolute/resolved paths (e.g. find_soffice()) as well as PATH names.
        candidate = Path(name)
        executable = str(candidate) if candidate.is_file() else shutil.which(name)
        if not executable:
            versions[name] = None
            return versions, f"required system binary {name} was not found"
        try:
            probe = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                check=False,
                timeout=5,
                text=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            versions[name] = None
            return versions, f"system binary {name} failed its health probe ({type(exc).__name__})"
        if probe.returncode != 0:
            versions[name] = None
            return versions, f"system binary {name} failed its health probe"
        lines = (probe.stdout or probe.stderr or "").strip().splitlines()
        # Some Windows builds emit empty --version text with exit 0.
        versions[name] = lines[0][:200] if lines else "present"
    return versions, None


class ParserRegistry:
    """Runtime registry. Readiness is probed rather than inferred from extras."""

    def __init__(self, plugins: tuple[ParserPlugin, ...] | None = None) -> None:
        if plugins is None:
            from .parsers import builtin_plugins

            plugins = builtin_plugins()
        self._plugins = {plugin.parser_id: plugin for plugin in plugins}
        self._capabilities = {
            plugin.parser_id: self._probe(plugin) for plugin in plugins
        }

    @staticmethod
    def _probe(plugin: ParserPlugin) -> ParserCapability:
        dependency_versions, dependency_error = _dependency_versions(plugin.dependencies)
        binary_versions, binary_error = _binary_versions(plugin.system_binaries)
        readiness = ParserReadiness.READY
        reason = dependency_error or binary_error
        if reason:
            readiness = ParserReadiness.UNAVAILABLE
        elif plugin.health_probe is not None:
            try:
                readiness, reason = plugin.health_probe()
            except Exception as exc:
                readiness = ParserReadiness.UNAVAILABLE
                reason = f"parser health probe failed ({type(exc).__name__})"
        if readiness is ParserReadiness.READY:
            try:
                parser = plugin.factory()
                if not callable(getattr(parser, "parse", None)):
                    raise TypeError("parser has no parse method")
            except Exception as exc:
                readiness = ParserReadiness.UNAVAILABLE
                reason = f"parser construction failed ({type(exc).__name__})"
        features = (
            plugin.degraded_features
            if readiness is ParserReadiness.DEGRADED
            and plugin.degraded_features is not None
            else plugin.features
        )
        return ParserCapability(
            parser_id=plugin.parser_id,
            parser_version=plugin.parser_version,
            mime_types=plugin.mime_types,
            extensions=plugin.extensions,
            features=features,
            limits=plugin.limits,
            isolation_required=plugin.isolation_required,
            readiness=readiness,
            reason=reason,
            dependencies=dependency_versions,
            system_binaries=binary_versions,
        )

    def capabilities(self) -> tuple[ParserCapability, ...]:
        return tuple(self._capabilities.values())

    def capability(self, parser_id: str) -> ParserCapability:
        try:
            return self._capabilities[parser_id]
        except KeyError as exc:
            raise DocumentFormatError("Unsupported document format") from exc

    def capability_for_extension(self, extension: str) -> ParserCapability | None:
        normalized = extension.lower()
        if normalized and not normalized.startswith("."):
            normalized = f".{normalized}"
        matches = [
            capability
            for capability in self._capabilities.values()
            if normalized in capability.extensions
        ]
        if not matches:
            return None
        return next((item for item in matches if item.available), matches[0])

    def resolve(self, *, mime_type: str, extension: str) -> tuple[ParserPlugin, ParserCapability]:
        normalized_extension = extension.lower()
        matches = [
            plugin
            for plugin in self._plugins.values()
            if mime_type in plugin.mime_types and normalized_extension in plugin.extensions
        ]
        if not matches:
            raise DocumentFormatError(
                f"Document MIME type {mime_type!r} is incompatible with extension "
                f"{normalized_extension or '<none>'!r}"
            )
        plugin = matches[0]
        capability = self._capabilities[plugin.parser_id]
        if not capability.available:
            raise DocumentUnavailableError(
                capability.reason or f"Parser {plugin.parser_id} is unavailable"
            )
        return plugin, capability


def get_parser_registry() -> ParserRegistry:
    """Return a fresh live readiness snapshot.

    Dependency installation and system-binary changes therefore take effect
    without restarting Kazma.
    """

    return ParserRegistry()
