"""Keep the unified-turn harness's provider config alive per test.

The root ``conftest.py`` gives every test its own ConfigStore
(process-singleton isolation, and it is right to). The harness seeds its
deterministic provider into the store that exists when the app BOOTS —
and the app re-reads the store on every turn, so by the time a test runs,
its seeds are in a store nobody is reading any more.

The HTTP harness never noticed: it pins no model, so the chat route uses
the agent's own provider and skips the registry. A browser pins the model
on every send, which sends the route through ``get_client(model)``, which
looks the model up in the CURRENT store, fails to find it, falls back to
the shipped OpenAI profile, and the pre-stream key check refuses the turn
with "No API key configured for https://api.openai.com/v1".

So the seeds are re-applied per test, after the root fixture has
installed its store. Autouse fixtures at the same scope run
outermost-first, so this one lands last, which is what makes it work.

Scoped by ``KAZMA_UTB_HARNESS``, set only while the harness is running:
no other e2e test has its provider configuration rewritten underneath it.

── Why every unified-turn suite boots one app per TEST ──────────────

The same isolation, seen from the other side. A module-scoped app
outlives the per-test swaps and ends up writing through stores the next
test has already replaced. Two symptoms, one cause:

* ``KeyError: 'session not found: utb-…'`` out of ``reply_sink.transact``
  — the app is holding the previous test's SessionManager;
* a pinned model falling back to the shipped OpenAI profile, because the
  provider seeds are in a ConfigStore nobody reads any more.

Booting per test costs about 25 seconds and makes the app's singletons
the test's singletons. That is the trade worth making: a shared server
that silently diverges from the test's own state is a harness reporting
on something other than the product.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _reseed_unified_turn_provider():
    if os.environ.get("KAZMA_UTB_HARNESS") != "1":
        yield
        return
    try:
        from tests.e2e._unified_turn_harness import seed_provider_config

        seed_provider_config()
    except Exception:  # noqa: BLE001 - never block a test on seeding
        pass
    yield
