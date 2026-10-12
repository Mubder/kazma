"""Offline pytest diagnostic: report browser-loop tasks before a test timeout."""
import os
import sys
import threading

_timer = None


def pytest_configure():
    # Already loaded in this interpreter. Workspace tools and launcher tests
    # spawn pytest in unrelated folders; they must not inherit this diagnostic
    # module, which is neither installed there nor part of their test contract.
    remaining = [name.strip() for name in os.environ.get('PYTEST_PLUGINS', '').split(',')
                 if name.strip() and name.strip() != __name__]
    if remaining:
        os.environ['PYTEST_PLUGINS'] = ','.join(remaining)
    else:
        os.environ.pop('PYTEST_PLUGINS', None)


def pytest_addoption(parser):
    parser.addoption('--browser-diagnostic-selection', action='store_true', default=False)


def pytest_collection_modifyitems(config, items):
    import json
    from pathlib import Path

    selection = Path(__file__).with_name('browser_order_selection_2026_10_11.json')
    if config.getoption('--browser-diagnostic-selection') and selection.is_file():
        names = set(json.loads(selection.read_text(encoding='utf-8')))
        selected = [item for item in items if item.path.name in names]
        config.hook.pytest_deselected(items=[item for item in items if item.path.name not in names])
        items[:] = selected


def _dump():
    import asyncio
    from kazma_core import playwright_loop

    loop = playwright_loop._loop
    print('BROWSER ORDER DIAGNOSTIC: loop', loop, 'thread', playwright_loop._thread, flush=True)
    def tasks():
        for task in asyncio.all_tasks(loop):
            print('BROWSER OWNED TASK:', repr(task), flush=True)
            task.print_stack(file=sys.stdout)
        sys.stdout.flush()
    if loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(tasks)


def pytest_runtest_setup(item):
    global _timer
    if item.path.name == 'test_browser_acquisition_broker.py':
        _timer = threading.Timer(20, _dump)
        _timer.daemon = True
        _timer.start()


def pytest_runtest_teardown(item):
    global _timer
    if _timer is not None:
        _timer.cancel()
        _timer = None
