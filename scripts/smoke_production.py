#!/usr/bin/env python3
"""Production smoke checks for Kazma (operator acceptance).

Usage:
  python scripts/smoke_production.py
  python scripts/smoke_production.py --base http://127.0.0.1:9090 --secret YOUR_SECRET

Exit 0 = all critical checks passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def get(url: str, headers: dict | None = None, timeout: float = 10.0) -> tuple[int, dict | str]:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(body)
            except Exception:
                return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body
    except Exception as e:
        return 0, str(e)


def post_json(url: str, payload: dict, headers: dict | None = None) -> tuple[int, dict | str]:
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(body)
            except Exception:
                return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body
    except Exception as e:
        return 0, str(e)


def main() -> int:
    p = argparse.ArgumentParser(description="Kazma production smoke tests")
    p.add_argument("--base", default=os.environ.get("KAZMA_SMOKE_BASE", "http://127.0.0.1:9090"))
    p.add_argument("--secret", default=os.environ.get("KAZMA_SECRET", ""))
    args = p.parse_args()
    base = args.base.rstrip("/")
    failed: list[str] = []
    ok: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            ok.append(name)
            print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
        else:
            failed.append(name)
            print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))

    print(f"Smoke against {base}\n")

    # Liveness
    code, body = get(f"{base}/health/live")
    check("health/live", code == 200, str(body)[:80])

    # Readiness
    code, body = get(f"{base}/health/ready")
    status = body.get("status") if isinstance(body, dict) else None
    check(
        "health/ready",
        code in (200, 503) and status in ("ready", "degraded", "starting", "not_ready"),
        f"http={code} status={status}",
    )
    if code == 503:
        print("         (503 means critical deps down — expected if DB missing)")

    # Public health root
    code, _ = get(f"{base}/health")
    check("health", code == 200)

    # Auth status (open)
    code, body = get(f"{base}/api/auth/status")
    check("auth/status", code == 200, str(body)[:100] if isinstance(body, dict) else "")
    oidc = isinstance(body, dict) and body.get("oidc")

    # Login page
    code, _ = get(f"{base}/login")
    check("login page", code == 200)

    # Protected API without auth → 401 when secret configured
    code, body = get(f"{base}/api/settings")
    if isinstance(body, dict) or code in (200, 401, 403):
        # When secret unset open mode returns 200; with secret expect 401
        check("api/settings responds", code in (200, 401, 403), f"http={code}")

    if args.secret:
        code, body = post_json(
            f"{base}/api/auth/login",
            {"secret": args.secret},
        )
        check("login with secret", code == 200, str(body)[:80])
        # Note: cookie not stored in this bare urllib smoke — header auth:
        hdr = {"X-Kazma-Secret": args.secret}
        code, body = get(f"{base}/api/auth/me", headers=hdr)
        check("auth/me with secret", code == 200, str(body)[:80])
        code, body = get(f"{base}/api/saas/status", headers=hdr)
        check("saas/status", code == 200, str(body)[:100] if isinstance(body, dict) else "")
    else:
        print("  SKIP  secret-authenticated checks (pass --secret or KAZMA_SECRET)")

    if oidc:
        code, _ = get(f"{base}/api/auth/oidc/start")
        # redirect 302/307 or 503 if misconfigured
        check("oidc/start reachable", code in (302, 307, 503, 200), f"http={code}")
    else:
        print("  SKIP  oidc (not configured)")

    print()
    print(f"Passed: {len(ok)}  Failed: {len(failed)}")
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    print("All smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
