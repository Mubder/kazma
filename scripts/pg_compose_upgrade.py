"""Move a Docker Compose deployment's database from Postgres 16 to 18.

    python scripts/pg_compose_upgrade.py -f docker-compose.postgres.yml [-p PROJECT] [-y]

Run it on the Docker host, in the folder that holds the compose file and its
.env, after the compose file names Postgres 18. Kazma's container is stopped
while the data is copied, and started again after. What it does and how it
puts Postgres 16 back when anything fails: kazma_core.postgres_compose_upgrade.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "kazma-core"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-f", "--file", required=True, help="the compose file (docker-compose.postgres.yml or .ha.yml)")
    parser.add_argument("-p", "--project", help="the compose project name, when it is not the folder's")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask before acting")
    options = parser.parse_args(argv)
    from kazma_core.postgres_compose_upgrade import upgrade_compose

    return upgrade_compose(options.file, project=options.project, yes=options.yes)


if __name__ == "__main__":
    sys.exit(main())
