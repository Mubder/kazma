"""Settings' log level applies when it is saved, as the page says (FA-114).

The 2026-11 functional audit: the hint said "Applies immediately", but the
save set only the root logger's level. ``setup_logging`` gives Kazma's four
loggers and the root's file and console handlers their own level, so DEBUG
reached the log only after a restart, and at WARNING Kazma's INFO records
still reached the handlers.

Through ``SettingsManager.save_logging_settings``, on a logging tree built by
``setup_logging``'s own shape: a ``kazma_core`` DEBUG record reaches the file
handler after DEBUG is saved, an INFO record does not after WARNING. The
negative control is the root-only ``setLevel``.
"""

from __future__ import annotations

import logging

import pytest

from kazma_core import logging_config


class _Capture(logging.Handler):
    def __init__(self, name: str, level: str) -> None:
        super().__init__(level)
        self.set_name(name)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def tree():
    """The levels ``setup_logging`` sets: Kazma's loggers, the root and its
    named file/console handlers, all at INFO."""
    root = logging.getLogger()
    saved = (root.level, list(root.handlers),
             {n: logging.getLogger(n).level for n in logging_config._KAZMA_LOGGERS})
    file_handler = _Capture("file", "INFO")
    console = _Capture("console", "INFO")
    root.handlers = [file_handler, console]
    root.setLevel("INFO")
    for name in logging_config._KAZMA_LOGGERS:
        logging.getLogger(name).setLevel("INFO")
    yield file_handler
    root.setLevel(saved[0])
    root.handlers = saved[1]
    for name, level in saved[2].items():
        logging.getLogger(name).setLevel(level)


def _save(level: str) -> None:
    from kazma_core.config_store import get_config_store
    from kazma_core.settings_manager import SettingsManager

    SettingsManager(get_config_store()).save_logging_settings({"level": level})


def _reaches(handler: _Capture, level: int, marker: str) -> bool:
    log = logging.getLogger("kazma_core.fa114")
    if log.isEnabledFor(level):
        record = log.makeRecord(log.name, level, __file__, 0, marker, (), None)
        for h in logging.getLogger().handlers:
            if record.levelno >= h.level:
                h.handle(record)
    return any(r.getMessage() == marker for r in handler.records)


def test_debug_reaches_the_file_after_it_is_saved(tree):
    assert not _reaches(tree, logging.DEBUG, "before")
    _save("DEBUG")
    assert _reaches(tree, logging.DEBUG, "after-debug")


def test_info_stops_after_warning_is_saved(tree):
    assert _reaches(tree, logging.INFO, "info-before")
    _save("WARNING")
    assert not _reaches(tree, logging.INFO, "info-after")
    assert _reaches(tree, logging.WARNING, "warning-after")


def test_the_root_alone_left_kazma_at_its_boot_level(tree):
    """Negative control: the save before FA-114 set only the root logger."""
    logging.getLogger().setLevel("DEBUG")
    assert not _reaches(tree, logging.DEBUG, "root-only")
