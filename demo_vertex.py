"""Demo — Verify Google Vertex AI Gemini connectivity via ADC.

Run:  python demo_vertex.py <your-gcp-project-id>
  or:  KAZMA_GCP_PROJECT=<id> python demo_vertex.py

This script is a standalone smoke test for the GoogleGeminiClient.
No domain logic, no agent scaffolding — just a clean ADC handshake
and a single text-generation round-trip.
"""

from __future__ import annotations

import logging
import os
import sys

# ── Path setup: ensure kazma-core is importable ─────────────────────
# When running from the repo root, the installed package is available
# via the venv.  If running raw, add the source tree.
_KAZMA_CORE = os.path.join(os.path.dirname(__file__), "kazma-core")
if os.path.isdir(_KAZMA_CORE):
    sys.path.insert(0, _KAZMA_CORE)

from kazma_core.google_llm import GoogleGeminiClient, GeminiAPIError

# ── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-7s] %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("demo_vertex")


# ── Configuration ──────────────────────────────────────────────────
# Either pass the project ID as the first CLI arg or set the env var.
_PROJECT_ID: str = ""


# ── Demo ────────────────────────────────────────────────────────────


def main() -> int:
    project_id = _PROJECT_ID
    if len(sys.argv) > 1:
        project_id = sys.argv[1]
    elif not project_id:
        project_id = os.environ.get("KAZMA_GCP_PROJECT", "")

    if not project_id:
        print(
            "ERROR: GCP Project ID is required.\n\n"
            "  Usage:  python demo_vertex.py <project-id>\n"
            "     or:  KAZMA_GCP_PROJECT=<id> python demo_vertex.py\n",
            file=sys.stderr,
        )
        return 1

    print(f"Initialising GoogleGeminiClient for project '{project_id}'...")
    print()

    try:
        client = GoogleGeminiClient(project_id=project_id)
    except (ValueError, GeminiAPIError) as exc:
        logger.error("Client initialisation failed: %s", exc)
        return 1

    # ── Simple inference ──────────────────────────────────────────
    prompt = (
        "In one paragraph, summarise the key benefits of modular code "
        "architecture for enterprise software projects."
    )

    print(f"PROMPT:  {prompt}")
    print()
    print("Awaiting Gemini response...")
    print("-" * 60)

    try:
        result = client.generate_text(prompt, temperature=0.2)
    except GeminiAPIError as exc:
        logger.error("Generation failed: %s", exc)
        return 1

    print(result)
    print("-" * 60)
    print()
    print("✓  Vertex AI Gemini connectivity verified successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
