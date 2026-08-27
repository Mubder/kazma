"""SafeTimedRotatingFileHandler — a Windows rotation race must not kill logging.

Observed 2026-08-27: another process held the rotated log target open at the
first post-midnight rollover; the stock handler raised inside emit() and the
production server silently fell back to console-only logging for the rest of
its lifetime.
"""

from __future__ import annotations

import logging

from kazma_core.logging_config import SafeTimedRotatingFileHandler


class TestSafeRotation:
    def test_rollover_failure_is_skipped_not_fatal(self, tmp_path, monkeypatch, capsys):
        log = tmp_path / "kazma.log"
        log.write_text("line\n", encoding="utf-8")
        h = SafeTimedRotatingFileHandler(
            str(log), when="midnight", interval=1, backupCount=3, encoding="utf-8"
        )
        h.setLevel(logging.INFO)
        calls = {"n": 0}

        def _collide(src, dest):
            calls["n"] += 1
            raise PermissionError(13, "The process cannot access the file")

        # Simulate the Windows collision: remove/rename of a held file fails.
        monkeypatch.setattr(h, "rotate", _collide)

        before = h.rolloverAt
        h.doRollover()  # must NOT raise
        assert calls["n"] == 1
        assert h.rolloverAt >= before
        captured = capsys.readouterr()
        assert "rotation skipped" in captured.err

        # The handler stays usable: emits keep flowing to the live file.
        rec = logging.LogRecord("t", logging.INFO, __file__, 1, "still alive", None, None)
        h.emit(rec)
        h.flush()
        assert "still alive" in log.read_text(encoding="utf-8")
        h.close()

    def test_normal_rotation_still_rotates(self, tmp_path):
        log = tmp_path / "kazma.log"
        log.write_text("old\n", encoding="utf-8")
        h = SafeTimedRotatingFileHandler(
            str(log), when="midnight", interval=1, backupCount=3, encoding="utf-8"
        )
        h.doRollover()
        # The pre-rotation content moved to a dated sibling (suffix date may
        # be yesterday or today depending on local time — accept either).
        siblings = [p.name for p in tmp_path.glob("kazma.log.*")]
        assert len(siblings) == 1, siblings
        assert "old\n" in (tmp_path / siblings[0]).read_text(encoding="utf-8")
        assert log.read_text(encoding="utf-8") == ""
        h.close()

    def test_setup_logging_wires_safe_handler(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KAZMA_LOG_FILE", str(tmp_path / "t.log"))
        monkeypatch.setenv("KAZMA_LOG_LEVEL", "INFO")
        import kazma_core.logging_config as _lc
        from kazma_core.logging_config import setup_logging

        # setup_logging is idempotent by design (_logging_configured guard) —
        # an earlier test in the same chunk process already configured it,
        # so force this test's own pass with ITS tmp target.
        monkeypatch.setattr(_lc, "_logging_configured", False)

        root = logging.getLogger()
        pre_handlers = list(root.handlers)
        setup_logging(level="INFO")
        added = [h for h in root.handlers if h not in pre_handlers]
        file_handlers = [
            h for h in root.handlers if isinstance(h, SafeTimedRotatingFileHandler)
        ]
        assert file_handlers, "setup_logging must attach the Safe rotation handler"
        logging.getLogger("kazma_core.test_rotation").info("canary")
        for h in file_handlers:
            h.flush()
        assert "canary" in (tmp_path / "t.log").read_text(encoding="utf-8")
        for h in file_handlers:
            h.close()
            root.removeHandler(h)
        # Drop any console handlers this call added too, so a later test's
        # guard-reset doesn't accumulate duplicates on root.
        for h in added:
            if h not in pre_handlers and h in root.handlers:
                root.removeHandler(h)
