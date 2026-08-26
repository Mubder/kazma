"""The ``lastEventId`` getter must close over ssePost's own scope.

Regression for the "lastEventId is not defined" ReferenceError: the
``var lastEventId`` was declared INSIDE the ``fetch().then(...)`` callback
while the returned object's ``lastEventId`` getter lived at ssePost's top
level — a different function scope — so the getter threw whenever an SSE
stream error path called it (onError → _noteSeq → activeStream.lastEventId()).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_STREAMING = (
    Path(__file__).resolve().parent.parent
    / "kazma-ui"
    / "kazma_ui"
    / "static"
    / "js"
    / "streaming.js"
)

_NODE_SCRIPT = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[1], 'utf8');
const sandbox = {
  window: {},
  fetch: () => new Promise(() => {}),   // never resolves -> .then callback never runs
  location: { protocol: 'http:', host: 'localhost' },
  WebSocket: function(){},
  AbortController,
  navigator: {},
  document: { createElement: () => ({ style:{}, setAttribute(){}, appendChild(){} }),
              getElementById: () => null, querySelector: () => null },
  console, setTimeout, clearTimeout,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const s = sandbox.KazmaStream.sse('/x', {}, {});
const id = s.lastEventId();   // ReferenceError here = scope regression
process.stdout.write(String(id == null ? 'null' : id));
"""


def test_last_event_id_getter_is_in_ssepost_scope() -> None:
    proc = subprocess.run(
        ["node", "-e", _NODE_SCRIPT, str(_STREAMING)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    # A fresh stream with a never-resolving fetch has no seq yet → null,
    # crucially WITHOUT throwing "lastEventId is not defined".
    assert proc.stdout.strip() == "null", proc.stdout
