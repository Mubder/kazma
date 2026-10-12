"""Every fresh browser context refuses private HTTP through the shared broker."""
import pytest

from tests.test_browser_acquisition_broker import chromium, origin, render_fixture


@pytest.mark.asyncio
async def test_real_chromium_contexts_each_block_loopback(chromium, origin):
    base, native_hits = origin
    for _ in range(3):
        result = (await render_fixture(base + '/private')).result
        assert not result.ok and result.error_code == 'security_blocked', result.evidence()
        assert native_hits == [], 'A fresh context contacted a private origin outside the broker'
