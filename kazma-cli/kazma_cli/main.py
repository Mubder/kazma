"""Kazma CLI main entry point."""

from __future__ import annotations

import sys


def main() -> None:
    """CLI entry point — placeholder."""
    print("Kazma CLI v0.1.0")
    print("Commands: init, status, migrate, serve")
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "status":
            print("Kazma status: OK")
        elif cmd == "serve":
            print("Starting Kazma UI...")
        else:
            print(f"Unknown command: {cmd}")
    else:
        print("Usage: kazma <command>")


if __name__ == "__main__":
    main()
