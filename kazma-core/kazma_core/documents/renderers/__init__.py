"""Generation and conversion plugin registry with live runtime readiness."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

__all__ = [
    "RendererCapability",
    "RendererPlugin",
    "RendererReadiness",
    "RendererRegistry",
    "get_renderer_registry",
]


class RendererReadiness(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class RendererPlugin:
    renderer_id: str
    renderer_version: str
    operations: tuple[str, ...]
    formats: tuple[str, ...]
    features: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    system_binaries: tuple[str, ...] = ()
    health_probe: Callable[[], tuple[RendererReadiness, str | None]] | None = None


@dataclass(frozen=True, slots=True)
class RendererCapability:
    renderer_id: str
    renderer_version: str
    operations: tuple[str, ...]
    formats: tuple[str, ...]
    features: tuple[str, ...]
    readiness: RendererReadiness
    reason: str | None
    dependencies: dict[str, str | None]
    system_binaries: dict[str, str | None]

    @property
    def available(self) -> bool:
        return self.readiness is not RendererReadiness.UNAVAILABLE


def _module_versions(names: tuple[str, ...]) -> tuple[dict[str, str | None], str | None]:
    versions: dict[str, str | None] = {}
    for value in names:
        module, _, distribution = value.partition(":")
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, ValueError):
            spec = None
        if spec is None:
            versions[module] = None
            return versions, f"required dependency {module} was not found"
        package = distribution or module.split(".", 1)[0]
        try:
            versions[module] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[module] = "present"
    return versions, None


@lru_cache(maxsize=16)
def _probe_binary_version(
    executable: str, modified_ns: int
) -> tuple[str | None, str | None]:
    del modified_ns
    try:
        result = subprocess.run(
            [executable, "--headless", "--version"],
            capture_output=True,
            check=False,
            timeout=5,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"health probe failed ({type(exc).__name__})"
    if result.returncode:
        return None, "headless health probe failed"
    lines = (result.stdout or result.stderr).strip().splitlines()
    return (lines[0][:200] if lines else "present"), None


def _binary_versions(names: tuple[str, ...]) -> tuple[dict[str, str | None], str | None]:
    versions: dict[str, str | None] = {}
    for name in names:
        executable = shutil.which(name)
        if not executable:
            versions[name] = None
            return versions, f"required system binary {name} was not found"
        try:
            modified_ns = Path(executable).stat().st_mtime_ns
        except OSError:
            modified_ns = 0
        version, error = _probe_binary_version(executable, modified_ns)
        versions[name] = version
        if error:
            return versions, f"{name} {error}"
    return versions, None


def builtin_plugins() -> tuple[RendererPlugin, ...]:
    return (
        RendererPlugin(
            "markdown",
            "1",
            ("generate:markdown", "generate:html", "convert:markdown:html"),
            ("markdown", "html"),
            ("unicode", "mixed-direction", "headings", "tables", "citations"),
        ),
        RendererPlugin(
            "reportlab",
            "1",
            ("generate:pdf",),
            ("pdf",),
            ("unicode", "headers", "footers", "page-numbers", "tables", "citations"),
            ("reportlab",),
        ),
        RendererPlugin(
            "python-docx",
            "1",
            ("generate:docx", "convert:markdown:docx"),
            ("docx",),
            ("unicode", "headings", "headers", "footers", "basic-toc", "citations"),
            ("docx:python-docx",),
        ),
        RendererPlugin(
            "openpyxl",
            "1",
            ("generate:xlsx",),
            ("xlsx",),
            ("unicode", "multiple-sheets", "styles"),
            ("openpyxl",),
        ),
        RendererPlugin(
            "python-pptx",
            "1",
            ("generate:pptx",),
            ("pptx",),
            ("unicode", "slides", "headings", "footers"),
            ("pptx:python-pptx",),
        ),
        RendererPlugin(
            "weasyprint",
            "1",
            ("convert:html:pdf", "convert:markdown:pdf"),
            ("html", "markdown", "pdf"),
            ("unicode", "mixed-direction", "local-approved-assets", "no-network"),
            ("weasyprint",),
        ),
        RendererPlugin(
            "libreoffice",
            "1",
            (
                "convert:doc:pdf",
                "convert:xls:xlsx",
                "convert:ppt:pptx",
                "convert:docx:pdf",
                "convert:xlsx:pdf",
                "convert:pptx:pdf",
            ),
            ("doc", "xls", "ppt", "docx", "xlsx", "pptx", "pdf"),
            ("headless", "isolated-profile", "no-network"),
            system_binaries=("soffice",),
        ),
    )


class RendererRegistry:
    def __init__(self, plugins: tuple[RendererPlugin, ...] | None = None) -> None:
        self._plugins = plugins or builtin_plugins()
        self._capabilities = tuple(self._probe(item) for item in self._plugins)

    @staticmethod
    def _probe(plugin: RendererPlugin) -> RendererCapability:
        dependencies, dep_error = _module_versions(plugin.dependencies)
        binaries, binary_error = _binary_versions(plugin.system_binaries)
        readiness = RendererReadiness.READY
        reason = dep_error or binary_error
        if reason:
            readiness = RendererReadiness.UNAVAILABLE
        elif plugin.health_probe is not None:
            try:
                readiness, reason = plugin.health_probe()
            except Exception as exc:
                readiness = RendererReadiness.UNAVAILABLE
                reason = f"renderer health probe failed ({type(exc).__name__})"
        detected_version = next(
            (
                value
                for value in (*dependencies.values(), *binaries.values())
                if value is not None
            ),
            plugin.renderer_version,
        )
        return RendererCapability(
            plugin.renderer_id,
            detected_version,
            plugin.operations,
            plugin.formats,
            plugin.features,
            readiness,
            reason,
            dependencies,
            binaries,
        )

    def capabilities(self) -> tuple[RendererCapability, ...]:
        return self._capabilities

    def resolve(self, operation: str) -> RendererCapability:
        for capability in self._capabilities:
            if operation in capability.operations:
                return capability
        raise ValueError(f"unsupported document operation: {operation}")


def get_renderer_registry() -> RendererRegistry:
    return RendererRegistry()
