"""One-shot live API smoke (uses KAZMA_SECRET from env or .env). Do not print secret.

Usage:
  export KAZMA_SECRET='…'   # must match the *running* server
  python scripts/live_api_smoke.py

401 Unauthorized usually means .env secret != process that started uvicorn.
Restart the server after .env changes, or export the live secret.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("KAZMA_SMOKE_BASE", "http://127.0.0.1:9090")


def load_secret() -> str:
    s = (os.environ.get("KAZMA_SECRET") or "").strip()
    if s:
        return s
    for name in (".env", ".env.local"):
        p = ROOT / name
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "KAZMA_SECRET":
                return v.strip().strip('"').strip("'")
    return ""


def req(method: str, path: str, body: dict | None = None, secret: str = "") -> tuple[int, object]:
    data = None
    headers = {"Accept": "application/json"}
    if secret:
        headers["X-Kazma-Secret"] = secret
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw[:500]
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:500]
    except Exception as e:
        return 0, {"error": str(e)}


def main() -> int:
    results: list[tuple] = []
    secret = load_secret()
    results.append(("secret_loaded", bool(secret)))

    code, body = req("GET", "/health")
    results.append(
        (
            "health",
            code == 200 and isinstance(body, dict) and body.get("status") == "ok",
            code,
        )
    )

    code, body = req("GET", "/api/research/ready", secret=secret)
    ready = isinstance(body, dict) and body.get("ready") is True
    results.append(("research_ready", ready, code))

    code, body = req("GET", "/api/research/ready?live=1", secret=secret)
    live_check = None
    if isinstance(body, dict):
        for c in body.get("checks") or []:
            if c.get("id") == "search_live":
                live_check = c
    results.append(
        (
            "research_ready_live",
            isinstance(body, dict) and body.get("ok") is True,
            code,
            live_check,
        )
    )

    code, body = req(
        "POST",
        "/api/memory/v2/eval/golden",
        body={"include_optional": False},
        secret=secret,
    )
    if isinstance(body, dict):
        results.append(
            (
                "golden_eval",
                bool(body.get("total")),
                code,
                f"pass {body.get('passed')}/{body.get('total')} rate={body.get('pass_rate')} ok={body.get('ok')}",
            )
        )
    else:
        results.append(("golden_eval", False, code, body))

    code, body = req(
        "POST",
        "/api/memory/v2/probe",
        body={"query": "favorite color teal", "limit": 5},
        secret=secret,
    )
    if isinstance(body, dict):
        results.append(
            (
                "memory_probe",
                body.get("ok") is True,
                code,
                f"beliefs={len(body.get('beliefs') or [])} episodes={len(body.get('episodes') or [])} empty={body.get('empty')}",
            )
        )
    else:
        results.append(("memory_probe", False, code, body))

    code, body = req("GET", "/api/research/sessions?limit=5", secret=secret)
    if isinstance(body, dict):
        n = body.get("count", len(body.get("sessions") or []))
        results.append(("research_sessions_list", True, code, f"count={n}"))
    else:
        results.append(("research_sessions_list", False, code, body))

    code, body = req("GET", "/api/research/papers?limit=5", secret=secret)
    if isinstance(body, dict):
        n = body.get("count", len(body.get("papers") or []))
        results.append(("research_papers_list", body.get("ok") is not False, code, f"count={n}"))
    else:
        results.append(("research_papers_list", False, code, body))

    kb_ok = False
    kb_detail = ""
    for p in ("/api/kb/libraries", "/api/knowledge/libraries"):
        code, body = req("GET", p, secret=secret)
        if code == 200:
            kb_ok = True
            if isinstance(body, dict):
                libs = body.get("libraries") or body.get("items") or body.get("data") or []
                kb_detail = f"{p} n={len(libs) if isinstance(libs, list) else '?'}"
            else:
                kb_detail = f"{p} ok"
            break
        kb_detail = f"{p}->{code}"
    results.append(("kb_libraries", kb_ok, kb_detail))

    print("=== LIVE API SMOKE ===")
    fails = 0
    for row in results:
        name, ok = row[0], row[1]
        mark = "PASS" if ok else "FAIL"
        if not ok:
            fails += 1
        print(f"  [{mark}] {name}: {row[2:] if len(row) > 2 else ''}")

    print("\n=== R1 brief research session (may take a few minutes) ===")
    code, body = req(
        "POST",
        "/api/research/sessions",
        body={
            "topic": "SQLite WAL mode concurrency smoke test",
            "depth": "brief",
            "max_sources": 3,
        },
        secret=secret,
    )
    sid = None
    if isinstance(body, dict) and body.get("session"):
        sid = body["session"].get("id")
        print(f"  start PASS code={code} id={sid} status={body['session'].get('status')}")
    else:
        print(f"  start FAIL code={code} body={body}")
        fails += 1

    if sid:
        deadline = time.time() + 300
        last = None
        terminal = None
        while time.time() < deadline:
            code, body = req("GET", f"/api/research/sessions/{sid}", secret=secret)
            if isinstance(body, dict) and body.get("session"):
                s = body["session"]
                st = (s.get("status"), s.get("stage"), (s.get("message") or "")[:100])
                if st != last:
                    print(f"  poll {st}")
                    last = st
                if s.get("status") in ("done", "error", "cancelled"):
                    terminal = s
                    break
            time.sleep(5)
        if terminal:
            ok = terminal.get("status") == "done"
            if not ok:
                fails += 1
            print(
                f"  R1/R2 [{'PASS' if ok else 'FAIL'}] status={terminal.get('status')} "
                f"sources={terminal.get('sources')} report={terminal.get('report_path')} "
                f"rubric={terminal.get('rubric_score')} msg={(terminal.get('message') or '')[:120]} "
                f"err={(terminal.get('error') or '')[:160]}"
            )
        else:
            fails += 1
            print("  R1/R2 FAIL timeout after 300s")
            # cancel so we don't leave a runaway job
            req("POST", f"/api/research/sessions/{sid}/cancel", secret=secret)
            print("  cancelled lingering session")

    print(f"\n=== SUMMARY fails={fails} ===")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
