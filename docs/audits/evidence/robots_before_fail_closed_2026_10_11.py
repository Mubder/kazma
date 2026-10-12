"""Historical witness: the audited HEAD checker authorizes a failed policy fetch.

Outside normal test discovery. Expected failure against the original checker;
the remediation contract lives in tests/test_web_robots.py.
"""
import ast
import asyncio
import logging
import subprocess
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlparse

import pytest


@pytest.mark.asyncio
async def test_original_checker_must_not_treat_http_503_as_permission(monkeypatch):
    source = subprocess.check_output(
        ['git', 'show', '9796e53:kazma-core/kazma_core/tools/web_research.py'],
        text=True, encoding='utf-8')
    node = next(node for node in ast.parse(source).body
                if isinstance(node, ast.AsyncFunctionDef) and node.name == '_load_robots_checker')
    namespace = {'Any': Any, 'asyncio': asyncio, 'urlparse': urlparse,
                 'logger': logging.getLogger(__name__),
                 '_central_fetch': AsyncMock(return_value=SimpleNamespace(ok=False, text=''))}
    monkeypatch.setattr('kazma_core.security.ssrf.validate_url', lambda *a, **k: ('93.184.216.34',))
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<audited-checker>', 'exec'), namespace)
    checker = await namespace['_load_robots_checker']('https://docs.example/docs/')
    permitted = checker is None or checker('https://docs.example/docs/private')
    assert not permitted, 'Original checker allowed crawling after a failed HTTP 503 robots fetch'
