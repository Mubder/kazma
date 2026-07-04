"""KCA Operational Pipeline — Institutional incident simulation.

Run:  python -m kazma_core.kca.main

This script demonstrates the full KCA agent pipeline:
1. Ingest an operational disruption event.
2. Route it through the Founder Analyst.
3. Feed both the original incident AND the analyst's output
   to the Risk Guardian for a complete security and continuity
   matrix evaluation.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import NoReturn

from kazma_core.kca.llm import GeminiClient, GeminiClientError
from kazma_core.kca.custom_agents import FounderAnalyst, RiskGuardian

# ── Logging ─────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-7s] %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("kca.pipeline")

# ── Incident simulation ─────────────────────────────────────────────

_SAMPLE_INCIDENT: str = (
    "Date: 2026-07-04  08:30 AST\n"
    "Severity: CRITICAL\n"
    "Type: Personnel — Key Contributor Departure\n\n"
    "Ahmed Al-Mansoori, the Principal Backend Architect and sole "
    "maintainer of our flagship trading engine (14 microservices, custom "
    "order-matching algorithm), submitted his immediate resignation "
    "this morning.  He has been with the firm for 7 years, authored "
    "~65% of the engine codebase, and is the ONLY person with production "
    "access to the trading engine's encryption key management module.\n\n"
    "Critical observations:\n"
    "- No succession plan exists for this role.\n"
    "- His notice period is 2 weeks (contractual).\n"
    "- Three high-priority client deliverables depend on the trading "
    "engine and are scheduled for Q3 2026.\n"
    "- He expressed willingness to provide handover documentation but "
    "has historically resisted sharing deep system knowledge.\n"
    "- The encryption key module has never been audited by a second "
    "engineer."
)


# ── Pipeline ────────────────────────────────────────────────────────


def run_pipeline(
    project_id: str,
    location: str = "us-central1",
    model_name: str = "gemini-2.5-flash",
    incident: str = _SAMPLE_INCIDENT,
) -> int:
    """Execute the full KCA incident analysis pipeline.

    Returns:
        0 on success, 1 on failure.
    """
    # ── 1. Initialise Gemini client ────────────────────────────────
    try:
        client = GeminiClient(
            project_id=project_id,
            location=location,
            model_name=model_name,
        )
    except (ValueError, GeminiClientError) as exc:
        logger.error("Failed to initialise GeminiClient: %s", exc)
        return 1

    # ── 2. Instantiate sub-agents ──────────────────────────────────
    founder = FounderAnalyst(client)
    guardian = RiskGuardian(client)

    # ── 3. Phase 1: Founder Analyst ────────────────────────────────
    logger.info("=" * 60)
    logger.info("PHASE 1: Founder Analyst — institutional memory & risk")
    logger.info("=" * 60)

    founder_prompt = (
        "Analyse the following operational disruption incident:\n\n"
        f"{incident}\n\n"
        "Provide your full structured assessment per your system "
        "instructions."
    )

    try:
        founder_output = founder.query(founder_prompt)
    except GeminiClientError as exc:
        logger.error("Founder Analyst failed: %s", exc)
        return 1

    print(f"\n{'─' * 60}")
    print(f"  {founder_output.agent_name}  |  {len(founder_output.raw_response)} chars")
    print(f"{'─' * 60}")
    print(founder_output.raw_response)
    print(f"{'─' * 60}\n")

    # ── 4. Phase 2: Risk Guardian ──────────────────────────────────
    logger.info("=" * 60)
    logger.info("PHASE 2: Risk Guardian — dependency + compliance matrix")
    logger.info("=" * 60)

    guardian_prompt = (
        "Evaluate the following incident AND the Founder Analyst's "
        "assessment below.  Provide a complete security and continuity "
        "matrix evaluation per your system instructions.\n\n"
        "=== ORIGINAL INCIDENT ===\n"
        f"{incident}\n\n"
        "=== FOUNDER ANALYST ASSESSMENT ===\n"
        f"{founder_output.raw_response}\n\n"
        "=== YOUR TASK ===\n"
        "Deliver your full structured risk assessment now."
    )

    try:
        guardian_output = guardian.query(guardian_prompt)
    except GeminiClientError as exc:
        logger.error("Risk Guardian failed: %s", exc)
        return 1

    print(f"\n{'─' * 60}")
    print(f"  {guardian_output.agent_name}  |  {len(guardian_output.raw_response)} chars")
    print(f"{'─' * 60}")
    print(guardian_output.raw_response)
    print(f"{'─' * 60}\n")

    logger.info("Pipeline complete. Both agents returned successfully.")
    return 0


# ── CLI entry point ─────────────────────────────────────────────────


def main() -> NoReturn:
    """Entry point for the KCA pipeline.

    Reads the GCP project ID from the ``KCA_PROJECT_ID`` environment
    variable or from the first CLI argument.

    Usage:
        python -m kazma_core.kca.main <project_id>
        KCA_PROJECT_ID=my-gcp-project python -m kazma_core.kca.main
    """
    project_id: str

    if len(sys.argv) > 1:
        project_id = sys.argv[1]
    else:
        project_id = os.environ.get("KCA_PROJECT_ID", "")

    if not project_id:
        print(
            "ERROR: GCP Project ID is required.\n\n"
            "  Usage:  python -m kazma_core.kca.main <project_id>\n"
            "     or:  KCA_PROJECT_ID=my-project python -m kazma_core.kca.main\n",
            file=sys.stderr,
        )
        sys.exit(1)

    sys.exit(run_pipeline(project_id=project_id))


if __name__ == "__main__":
    main()
