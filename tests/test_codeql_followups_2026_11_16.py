"""Two CodeQL findings on the 2026-11-15 code, fixed in the code (2026-11-16).

- A backup's name came from the request: ``re.match(r"^\\d+$", name)`` passes
  ``"123\\n"`` (``$`` matches before a final newline), and the path was built
  from the text sent. ``universal._backup_dir_name`` rebuilds the name from
  its number before any path is made (the old check is the control).
- ``[A-Z][A-Z0-9_]*_KEY`` (the env-name rule in ``config_store`` and
  ``config_mask``) was read as polynomial: the ``_`` of the class overlaps the
  ``_KEY`` that follows. Two plain checks replace it; they give the old
  pattern's answer for every name tried, and stay linear on a long one.
"""

from __future__ import annotations

import random
import re
import string
import time

from kazma_core.backup import universal
from kazma_core.config_store import is_sensitive_config_key
from kazma_core.security.config_mask import env_name_is_secret

_OLD_NAME = re.compile(r"^\d+$")
_OLD_KEY = re.compile(r"[A-Z][A-Z0-9_]*_KEY")


def test_a_backup_name_is_its_number():
    assert universal._backup_dir_name("1794700000") == "1794700000"
    for bad in ("123\n", "../1", "1/2", "", "12a", "١٢", "1" * 21, " 12"):
        assert universal._backup_dir_name(bad) is None, repr(bad)


def test_control_the_old_check_let_a_newline_through():
    assert _OLD_NAME.match("123\n")
    assert universal._backup_dir_name("123\n") is None


def test_archive_and_delete_refuse_a_name_with_a_newline():
    assert universal.archive_universal_backup("123\n")["ok"] is False
    assert universal.delete_universal_backup("123\n")["ok"] is False


def test_the_key_rule_answers_as_the_old_pattern():
    rng = random.Random(7)
    alphabet = string.ascii_uppercase + string.digits + "_" + "abc."
    names = ["OPENAI_KEY", "A_KEY", "_KEY", "KEY", "AB_KEYX", "openai_key", "X__KEY", "9_KEY"]
    names += ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12))) + rng.choice(["", "_KEY"])
              for _ in range(3000)]
    for name in names:
        expected = bool(_OLD_KEY.fullmatch(name))
        assert env_name_is_secret(name) is expected, name
        if expected:
            assert is_sensitive_config_key(f"mcp.servers.x.env.{name}"), name


def test_the_key_rule_is_linear_on_a_long_name():
    hostile = "A" + "_" * 200_000 + "X"
    start = time.perf_counter()
    env_name_is_secret(hostile)
    is_sensitive_config_key(hostile)
    assert time.perf_counter() - start < 1.0
