"""A raw settings read must hold the store lock throughout its SQLite statement."""
import threading
from concurrent.futures import ThreadPoolExecutor

from kazma_core.config_store import ConfigStore


def test_live_raw_read_blocks_other_handle_users_and_close(tmp_path):
    store = ConfigStore(str(tmp_path / 'settings.db'))
    store.set('acquisition.example', {'reserved': 1})
    entered, release = threading.Event(), threading.Event()
    statements = []
    def trace(sql):
        if sql.startswith('SELECT value FROM settings WHERE key ='):
            statements.append(store._lock._is_owned())
            entered.set()
            assert release.wait(3), 'Raw-read fixture was not released'
    store._conn.set_trace_callback(trace)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(store._stored_raw, 'acquisition.example')
            try:
                assert entered.wait(3), 'The actual SQLite statement was not reached'
                acquired = store._lock.acquire(blocking=False)
                if acquired:
                    store._lock.release()
                assert not acquired, 'Another handle user/close could enter during the live raw read'
            finally:
                release.set()
            assert future.result() == {'reserved': 1}
        assert statements == [True]
        # The original boundary (cache lock only) must fail this same probe.
        statements.clear()
        with store._lock:
            pass
        row = store._conn.execute('SELECT value FROM settings WHERE key = ?', ('acquisition.example',)).fetchone()
        assert row is not None and statements == [False]
    finally:
        release.set()
        store.close()
