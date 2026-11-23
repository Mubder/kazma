"""What an own-key turn keeps off: APIs paid with the install's keys outside
the model client (Spaces S2-04 / S2-02, the review of de6874f3f).

A turn let in past its person's daily limit runs on their own model keys
only (``daily_limits.own_keys_only``): ``LLMProvider._person_call`` refuses a
model call that would go out on the install's key. A tool that pays another
API with the install's key, outside ``LLMProvider``, is never asked there:
"draw me 20 posters" went to the install's image API, with no ledger row,
from a turn the limit had stopped before. Each such path is kept off here:

* :data:`INSTALL_PAID_TOOLS` -- tools that can pay an API with the
  install's key; :func:`own_key_refusal` refuses the paid path in an own-key
  turn, before the card (``safety.call_preflight``) and at the call (the
  tool raises ``RefusedBeforeEffect``). ``generate_image`` keeps its keyless
  backend (pollinations): ``auto`` and the install's default
  (``KAZMA_IMAGE_PROVIDER``) draw with it in an own-key turn
  (``image_backends.router``), and a paid backend the call names is refused.
* the acquisition policy's remote providers (Tavily, Jina, Firecrawl, the
  scraping proxy): ``AcquisitionPolicy.permits`` answers no in an own-key
  turn, so search and reading go on locally (``web_acquire.policy``).
* E2B: ``sandbox.e2b.e2b_enabled`` answers no in an own-key turn; code runs
  where the execution policy allows otherwise, or not at all.

:data:`PAID_KEY_READERS` declares every tool-reachable module that reads an
API key from the environment, and how an own-key turn keeps off what it
pays; ``tests/test_install_paid_tools.py`` fails on one that is not declared.

The one accepted exception: the remote embedder (``memory.embedder``, when
the owner chose a remote embedding model) embeds a turn's question for
recall on the install's key. It costs very little and recall needs it.
"""

from __future__ import annotations

__all__ = ["INSTALL_PAID_TOOLS", "PAID_KEY_READERS", "own_key_refusal"]

#: Tools that can pay an API with the install's key outside ``LLMProvider``:
#: their paid path is refused in an own-key turn (:func:`own_key_refusal`).
#: A new one is a row here and a case there.
INSTALL_PAID_TOOLS: dict[str, str] = {
    "generate_image": "an image API (OpenAI, Stability or fal) paid with the install's key",
}

#: Every tool-reachable module that reads an API key from the environment ->
#: how an own-key turn keeps off what it pays (or why nothing is paid in a
#: turn). Held by ``tests/test_install_paid_tools.py`` both ways.
PAID_KEY_READERS: dict[str, str] = {
    "kazma-core/kazma_core/tools/image_backends/dall_e.py": "generate_image: refused (INSTALL_PAID_TOOLS)",
    "kazma-core/kazma_core/tools/image_backends/flux.py": "generate_image: refused (INSTALL_PAID_TOOLS)",
    "kazma-core/kazma_core/tools/image_backends/stability.py": "generate_image: refused (INSTALL_PAID_TOOLS)",
    "kazma-core/kazma_core/tools/image_backends/router.py": "generate_image: refused (INSTALL_PAID_TOOLS)",
    "kazma-core/kazma_core/tools/web_search.py": "Tavily: AcquisitionPolicy.permits says no; the free chain answers",
    "kazma-core/kazma_core/tools/read_url.py": "Jina and Firecrawl: AcquisitionPolicy.permits says no; read locally",
    "kazma-core/kazma_core/tools/research_readiness.py": "reads whether a key is set; spends nothing",
    "kazma-core/kazma_core/web_acquire/credits.py": "the admin's explicit balance refresh; no tool, no turn",
    "kazma-core/kazma_core/sandbox/e2b.py": "E2B: e2b_enabled says no; code runs where the policy allows, or not",
    "kazma-core/kazma_core/voice/stt.py": "speech to text of a message, before its turn starts: no tool",
    "kazma-core/kazma_core/voice/tts.py": "a reply read aloud after its turn: no tool",
    "kazma-core/kazma_core/voice/livekit.py": "a live voice room's sign-in; no model API",
    "kazma-core/kazma_core/x_api/auth_tokens.py": "the owner's space's own X account (S1-17b); not the install's",
    "kazma-core/kazma_core/x_api/config.py": "the owner's space's own X account (S1-17b); not the install's",
    "kazma-core/kazma_core/documents/extract_salvage.py": (
        "a remote document parser (LlamaParse, Reducto), outside any turn: a job for a person past their "
        "daily limit parses locally only (documents.ingestion, daily_limits.call_refusal)"),
    "kazma-core/kazma_core/memory/embedder.py": (
        "the remote embedder: the one accepted exception (a turn's question embedded for recall)"),
    "kazma-core/kazma_core/web_acquire/research_pipeline_job.py": (
        "fingerprints the configuration (a digest of these variables); spends nothing"),
    "kazma-skills/kazma_skills/native/email_manager/credentials.py": (
        "a space's own mail and calendar account (S1-17a); the install's app registration pays for nothing"),
}


def _paid_path(name: str, arguments: dict) -> bool:
    """Whether this call of *name* asks for a path paid with the install's
    key. For ``generate_image``, only a paid backend the call names: one that
    comes from the install's default (``KAZMA_IMAGE_PROVIDER``, ``auto``) is
    not refused -- the router draws with the keyless backend in an own-key
    turn (the review of 3faf46175: with ``KAZMA_IMAGE_PROVIDER=dall-e``
    every image in such a turn was refused)."""
    if name == "generate_image":
        from kazma_core.tools.image_backends.router import PAID_BACKENDS

        asked = str(arguments.get("provider") or "").strip().lower()
        return asked in PAID_BACKENDS
    return name in INSTALL_PAID_TOOLS


def own_key_refusal(name: str, arguments: object = None, user_text: str | None = None) -> str | None:
    """Why this call of *name* is refused, or None: an own-key turn
    (``daily_limits.own_keys_only``) and a call of :data:`INSTALL_PAID_TOOLS`
    that would pay with the install's key. In the person's language when
    *user_text* is given, else in both. In memory."""
    if name not in INSTALL_PAID_TOOLS:
        return None
    from kazma_core.spaces.daily_limits import own_keys_only

    if not own_keys_only() or not _paid_path(name, arguments if isinstance(arguments, dict) else {}):
        return None
    english = ("You reached your daily limit, and this turn runs on your own model key only: "
               f"this call of {name} would use {INSTALL_PAID_TOOLS[name]}, so it was not run.")
    arabic = (f"بلغت حدك اليومي، وهذا الطلب يعمل بمفتاحك الخاص فقط: هذا الاستدعاء لـ {name} كان "
              "سيستخدم خدمة مدفوعة بمفتاح التثبيت، فلم يُشغَّل.")
    if user_text:
        from kazma_core.language_lock import in_their_language

        return in_their_language(user_text, english, arabic)
    return english + "\n" + arabic
