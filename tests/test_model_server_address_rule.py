"""A model server's address is on this computer, the owner's network, or the
public internet -- never a cloud metadata or link-local address.

2026-11-16 (SonarCloud review): ``GET /api/models?provider=lm-studio&base_url=``
and ``/v1/models`` handed the query's address to model discovery, which
checked it with ``validate_url(..., allow_private=True)``. That flag returns
before every check, so the server asked ``169.254.169.254`` (cloud metadata)
and any internal host and port. Any operator credential reached the route,
and so did a link the signed-in owner opened (a GET carries the cookie, and
the CSRF check covers writes only). Now every caller asks
``security.ssrf.check_model_server_url`` (the providers rule, §60), and the
route is the administrator's.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def resolve(monkeypatch):
    """Name lookups answered locally: a name maps to the addresses given."""
    from kazma_core.security import ssrf

    names: dict[str, list[str]] = {}

    def fake(host: str) -> list[str]:
        return list(names.get(host, []))

    monkeypatch.setattr(ssrf, "_resolve_host_ips", fake)
    return names


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://[fd00:ec2::254]/latest/",
    "http://100.100.100.200/latest/",
    "http://169.254.10.1:8080/v1/models",
    "http://metadata.evil.example/v1/models",
    "file:///etc/passwd",
    "gopher://127.0.0.1:6379/",
])
def test_metadata_link_local_and_other_schemes_are_refused(resolve, url):
    from kazma_core.security.ssrf import SSRFError, check_model_server_url

    resolve["metadata.evil.example"] = ["169.254.169.254"]
    with pytest.raises(SSRFError):
        check_model_server_url(url)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:1234/v1/models",
    "http://localhost:11434/api/tags",
    "http://192.168.1.20:8000/v1/models",
    "http://100.101.102.103:8000/v1/models",
    "http://gpu-box:8000/v1/models",
    "https://api.example.com/v1/models",
])
def test_this_computer_the_owners_network_and_public_hosts_pass(resolve, url):
    from kazma_core.security.ssrf import check_model_server_url

    resolve["localhost"] = ["127.0.0.1"]
    resolve["gpu-box"] = ["10.0.0.7"]
    resolve["api.example.com"] = ["93.184.216.34"]
    check_model_server_url(url)


def test_negative_control_the_old_check_let_metadata_through():
    """The flag every caller used returns before any check."""
    from kazma_core.security.ssrf import validate_url

    assert validate_url("http://169.254.169.254/latest/", block_unresolved=True,
                        allow_private=True) == ()


@pytest.mark.parametrize("provider", ["lm-studio", "ollama", "custom", "openai"])
async def test_discovery_never_asks_the_metadata_address(resolve, monkeypatch, provider):
    """Behaviour: every discovery path refuses before a request is made."""
    import httpx

    from kazma_core.models.discovery import discover_models

    asked: list[str] = []

    async def record(self, url, *args, **kwargs):
        asked.append(str(url))
        raise AssertionError(f"asked {url}")

    monkeypatch.setattr(httpx.AsyncClient, "get", record)
    info = await discover_models(provider, base_url="http://169.254.169.254/v1")
    assert asked == []
    assert not info.models


def test_the_model_list_route_is_the_administrators():
    from kazma_core.security.platform_rbac import role_allows

    for path in ("/api/models", "/v1/models", "/api/models/"):
        assert not role_allows("operator", path, "GET"), path
        assert not role_allows("viewer", path, "GET"), path
        assert role_allows("admin", path, "GET"), path
    # Its siblings the chat page reads stay with every signed-in role.
    assert role_allows("operator", "/api/models/options", "GET")


def _product_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z", "--", "kazma-*", "scripts"], cwd=REPO,
                         capture_output=True, check=True).stdout.decode("utf-8")
    return [REPO / rel for rel in out.split("\0")
            if rel.endswith(".py") and "/tests/" not in rel]


def _passes_private(source: str) -> list[int]:
    """Lines that call something with ``allow_private`` not literally False."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "allow_private" and not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is False):
                lines.append(node.lineno)
    return lines


def test_no_product_code_lets_every_address_through():
    """Gate: ``allow_private`` skips every check, metadata included. An
    address given for a model server goes through check_model_server_url."""
    found = []
    for path in _product_files():
        text = path.read_text(encoding="utf-8")
        if "allow_private" in text:
            found += [f"{path.relative_to(REPO)}:{n}" for n in _passes_private(text)]
    assert found == [], found


def test_the_gate_sees_the_old_call():
    """Negative control: the discovery call as it was."""
    old = ("await asyncio.to_thread(validate_url, url, block_unresolved=True,"
           " allow_private=True)\n")
    assert _passes_private(old) == [1]
    assert _passes_private("validate_url(u, allow_private=flag)\n") == [1]
    assert _passes_private("validate_url(u, allow_private=False)\n") == []


_KEY_IN_ADDRESS = __import__("re").compile(
    r"[?&](?:api_key|apikey|token|secret|password)=(?:\$\{|'\s*\+|\"\s*\+)")


def _scripts_with_a_key_in_an_address(text: str) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), 1) if _KEY_IN_ADDRESS.search(line)]


def test_no_page_script_puts_a_key_in_an_address():
    """Gate: Settings sent the provider's key as ``&api_key=`` in the model
    list's address (logged, kept in history; the route read a header)."""
    roots = [REPO / "kazma-ui/kazma_ui/static/js", REPO / "kazma-ui/kazma_ui/templates"]
    found = []
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix in (".js", ".html") and "vendor" not in path.parts:
                lines = _scripts_with_a_key_in_an_address(path.read_text(encoding="utf-8"))
                found += [f"{path.relative_to(REPO)}:{n}" for n in lines]
    assert found == [], found


def test_the_key_gate_sees_the_old_line():
    """Negative control: models.js as it was."""
    old = "        if (api_key) url += `&api_key=${encodeURIComponent(api_key)}`;\n"
    assert _scripts_with_a_key_in_an_address(old) == [1]
