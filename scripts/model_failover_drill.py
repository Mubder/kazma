"""Run the backup-model drill now (resilience plan F2).

The maintenance cadence runs it once a month; this runs it by hand, from the
install's folder:

    .venv/Scripts/python.exe scripts/model_failover_drill.py [--json]

The primary model is forced down for one test prompt, the backup of another
vendor must answer it, the failover notice must name it (held: nothing is
paged), and the primary must answer again. Exit 0 when every step passed.
See kazma_core/observability/failover_drill.py.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    from kazma_core.env_files import load_env_files

    load_env_files()
    from kazma_core.observability.failover_drill import main

    sys.exit(main())
