"""The Linux clean-machine drill (``scripts/linux_drill.py``, REMAINING 2.3) asks
for what exists: every part it leaves out is a part of the install table, and
every option it hands ``setup install`` is one that command takes. The drill
itself runs on a WSL distro made for it (opt-in, from Windows); this keeps its
words true between runs.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _drill():
    spec = importlib.util.spec_from_file_location("linux_drill", ROOT / "scripts" / "linux_drill.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_parts_it_leaves_out_are_parts_of_the_install() -> None:
    from kazma_core.install_components import COMPONENTS

    ids = {c.id for c in COMPONENTS}
    drill = _drill()
    assert set(drill.WITHOUT) <= ids, sorted(set(drill.WITHOUT) - ids)
    # The negative control: a name the table does not have is caught.
    assert not {"no_such_part"} <= ids


def test_every_option_it_passes_is_one_the_commands_take() -> None:
    text = (ROOT / "scripts" / "linux_drill.py").read_text(encoding="utf-8")
    install = (ROOT / "kazma-cli" / "kazma_cli" / "install.py").read_text(encoding="utf-8")
    for option in ("--without", "--no-browser", "--check"):
        assert option in text, option
        assert f'"{option}"' in install, option
    setup_sh = (ROOT / "setup.sh").read_text(encoding="utf-8")
    for option in ("--mode", "--port"):
        assert re.search(rf"^\s*{re.escape(option)}\)", setup_sh, re.M) or option in setup_sh, option
    assert "kazma_cli update -y" in text and "kazma_cli uninstall -y" in text
