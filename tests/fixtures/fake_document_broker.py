#!/usr/bin/env python3
"""A stand-in for the document worker broker, for testing the guard.

The real broker (``kazma_core/documents/worker_broker.py``) needs Docker and a
worker image, and attests a container before it answers. This one speaks the
same ``status`` operation over the same framing, with the same token check,
and can be told -- through a JSON file beside its token file, read at every
start, because the guard hands the broker a scrubbed environment -- to fail in
each way the guard must handle:

    {"exit_code_at_start": 4}   exit at once with this code (4 no image,
                                5 no Docker, 2 cannot start)
    {"exit_after_s": 3}         exit (code ``exit_code``, default 1) once ready
    {"ready_delay_s": 2}        attest this long before listening
    {"never_listen": true}      run, never answer
    {"stop_answering_after_s": 3}  keep running, stop answering
    {"ignore_stop": true}       ignore the graceful stop (only a kill works)
    {"refuse_first_port": true} the first address it is given cannot be listened
                                on (exit 7, as a port Windows reserved)

Each start, answer and stop is appended to ``fake_broker.marker.jsonl`` beside
the token file. Standard library only, like the real one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import socketserver
import struct
import sys
import threading
import time
from pathlib import Path

REQUEST_MAGIC = b"KZDB-REQ/1\n"


def _exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--listen", required=True)
    parser.add_argument("--name", default="default")
    parser.add_argument("--fallback-to-newest-built", action="store_true")
    args = parser.parse_args()
    here = args.token_file.parent
    marker = here / "fake_broker.marker.jsonl"
    try:
        config = json.loads((here / "fake_broker.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}

    def note(event: str, **extra: object) -> None:
        with marker.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event": event, "pid": os.getpid(), "ts": time.time(), **extra}) + "\n")

    note("spawned", argv=sys.argv[1:], env_keys=sorted(os.environ))
    if config.get("exit_code_at_start") is not None:
        note("exit_at_start", code=int(config["exit_code_at_start"]))
        return int(config["exit_code_at_start"])
    refused = here / "fake_broker.refused_port"
    if config.get("refuse_first_port") and not refused.exists():
        refused.write_text(args.listen, encoding="utf-8")
        note("port_refused", listen=args.listen)
        return 7
    if config.get("refuse_first_port") and refused.read_text(encoding="utf-8") == args.listen:
        note("port_refused", listen=args.listen)
        return 7
    token = args.token_file.read_text(encoding="utf-8").strip()
    time.sleep(float(config.get("ready_delay_s", 0)))
    if config.get("never_listen"):
        while True:
            time.sleep(1)
    host, _, port = args.listen[4:].rpartition(":")
    started = time.monotonic()
    state = {"answering": True}

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            sock = self.request
            if not state["answering"]:
                time.sleep(30)
                return
            sock.settimeout(5)
            try:
                magic = _exact(sock, len(REQUEST_MAGIC))
                (size,) = struct.unpack(">I", _exact(sock, 4))
                header = json.loads(_exact(sock, size))
            except (OSError, ValueError, struct.error):
                return
            if magic != REQUEST_MAGIC or header.get("token") != token:
                body = {"status": "rejected", "message": "authentication failed"}
            else:
                body = {
                    "status": "ok", "protocol": 1, "ready": True, "broker": args.name,
                    "image": args.image, "image_ref": args.image, "image_fallback": False,
                    "image_id": "sha256:" + hashlib.sha256(args.image.encode()).hexdigest(),
                    "image_source": None, "attestation": {"verified": True, "problems": []},
                    "capabilities": {}, "active": 0, "quarantined": 0,
                }
            raw = json.dumps(body).encode("utf-8")
            sock.sendall(b"S" + struct.pack(">I", len(raw)) + raw)

    server = socketserver.ThreadingTCPServer((host, int(port)), Handler)
    server.daemon_threads = True
    note("listening", port=int(port))

    def stop(*_args: object) -> None:
        if config.get("ignore_stop"):
            note("stop_ignored")
            return
        note("graceful_exit")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, stop)

    def watch() -> None:
        while True:
            time.sleep(0.2)
            alive = time.monotonic() - started
            if config.get("exit_after_s") is not None and alive >= float(config["exit_after_s"]):
                note("exit_after", code=int(config.get("exit_code", 1)))
                os._exit(int(config.get("exit_code", 1)))
            if (config.get("stop_answering_after_s") is not None
                    and alive >= float(config["stop_answering_after_s"])):
                state["answering"] = False

    threading.Thread(target=watch, daemon=True).start()
    server.serve_forever(poll_interval=0.2)
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
