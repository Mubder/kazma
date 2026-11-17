"""Rebuild Kazma's catalog of X API operations from X's own OpenAPI spec.

    python scripts/x_api_catalog.py            # say what X added or removed
    python scripts/x_api_catalog.py --write    # rewrite the catalog
    python scripts/x_api_catalog.py --spec FILE  # from a saved spec

The catalog (``kazma-core/kazma_core/x_api/x_api_operations.json``) is the
spec reduced to what a call needs: each operation's id, method, path, the
authentication X accepts, its parameters (type, pattern, allowed values,
range) and its body's fields, plus the OAuth 2.0 scopes. Kazma's own rules on
top of it (which operations are refused, which are streams) are in
``kazma_core/x_api/operations.py``. A new operation X adds is callable once
the catalog is rewritten; tests/test_x_api_operations.py holds the rules to
every operation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SPEC_URL = "https://api.x.com/2/openapi.json"
ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "kazma-core" / "kazma_core" / "x_api" / "x_api_operations.json"
_AUTH = {"UserToken": "user", "OAuth2UserToken": "oauth2", "BearerToken": "app"}


def _resolver(spec: dict[str, Any]):
    def resolve(node: Any) -> Any:
        for _ in range(12):
            if not (isinstance(node, dict) and "$ref" in node):
                break
            target: Any = spec
            for part in node["$ref"].split("/")[1:]:
                target = target[part]
            node = target
        return node

    return resolve


def reduce(spec: dict[str, Any]) -> dict[str, Any]:
    """The catalog *spec* means."""
    resolve = _resolver(spec)

    def merged(schema: Any) -> tuple[dict[str, Any], set[str]]:
        schema = resolve(schema)
        props: dict[str, Any] = {}
        required = set(schema.get("required", []))
        for part in schema.get("allOf", []):
            p, r = merged(part)
            props.update(p)
            required |= r
        props.update(schema.get("properties", {}))
        return props, required

    def param(raw: Any) -> dict[str, Any]:
        p = resolve(raw)
        s = resolve(p.get("schema", {}))
        out: dict[str, Any] = {"name": p["name"], "in": p["in"]}
        if p.get("required"):
            out["required"] = True
        kind = s.get("type")
        if kind:
            out["type"] = kind
        for key in ("pattern", "minimum", "maximum", "minLength", "maxLength", "format"):
            if key in s:
                out[key] = s[key]
        if "enum" in s:
            out["enum"] = s["enum"]
        if kind == "array":
            item = resolve(s.get("items", {}))
            if "enum" in item:
                out["items_enum"] = item["enum"]
            if "pattern" in item:
                out["items_pattern"] = item["pattern"]
            if "maxItems" in s:
                out["max_items"] = s["maxItems"]
        about = (p.get("description") or "").strip().split("\n")[0][:160]
        if about:
            out["about"] = about
        return out

    operations = []
    for path, item in sorted(spec["paths"].items()):
        common = item.get("parameters", [])
        for method in ("get", "post", "put", "delete", "patch"):
            op = item.get(method)
            if not op:
                continue
            security = op.get("security", spec.get("security", []))
            entry: dict[str, Any] = {
                "id": op["operationId"],
                "tag": (op.get("tags") or ["General"])[0],
                "method": method.upper(),
                "path": path,
                "summary": op.get("summary", ""),
                "auth": [_AUTH[k] for k in sorted({k for s in security for k in s}) if k in _AUTH],
                "params": [param(p) for p in common + op.get("parameters", [])],
            }
            scopes = sorted({sc for s in security for sc in s.get("OAuth2UserToken", [])})
            if scopes:
                entry["scopes"] = scopes
            body = op.get("requestBody")
            if body:
                content = resolve(body).get("content", {})
                if "application/json" in content:
                    props, required = merged(content["application/json"].get("schema", {}))
                    entry["body"] = {"type": "json", "required": sorted(required), "fields": sorted(props)}
                else:
                    entry["body"] = {"type": next(iter(content), "unknown")}
            operations.append(entry)
    flow = spec["components"]["securitySchemes"]["OAuth2UserToken"]["flows"]["authorizationCode"]
    return {
        "source": SPEC_URL,
        "spec_version": spec["info"]["version"],
        "oauth2": {
            "authorize_url": flow["authorizationUrl"],
            "token_url": flow["tokenUrl"],
            "scopes": dict(sorted(flow["scopes"].items())),
        },
        "operations": operations,
    }


def render(catalog: dict[str, Any]) -> str:
    """One operation a line, so a change X makes reads as a change of lines."""
    head = {k: v for k, v in catalog.items() if k != "operations"}
    lines = [json.dumps(op, ensure_ascii=False, separators=(",", ":")) for op in catalog["operations"]]
    text = json.dumps(head, ensure_ascii=False, separators=(",", ":"))[:-1]
    return text + ',"operations":[\n' + ",\n".join(lines) + "\n]}\n"


def _fetch() -> dict[str, Any]:
    import httpx

    with httpx.Client(timeout=60.0, follow_redirects=False) as client:
        resp = client.get(SPEC_URL, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true", help="rewrite the catalog")
    parser.add_argument("--spec", help="a saved copy of X's spec instead of the live one")
    args = parser.parse_args(argv)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8")) if args.spec else _fetch()
    new = reduce(spec)
    old = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.is_file() else {"operations": []}
    old_ids = {o["id"] for o in old.get("operations", [])}
    new_ids = {o["id"] for o in new["operations"]}
    print(f"X API spec {new['spec_version']}: {len(new_ids)} operations "
          f"(catalog: {old.get('spec_version', 'none')}, {len(old_ids)})")
    for name in sorted(new_ids - old_ids):
        print(f"  + {name}")
    for name in sorted(old_ids - new_ids):
        print(f"  - {name}")
    if args.write:
        CATALOG.write_text(render(new), encoding="utf-8", newline="\n")
        print(f"wrote {CATALOG.relative_to(ROOT)}")
        return 0
    return 1 if (new_ids ^ old_ids) else 0


if __name__ == "__main__":
    sys.exit(main())
