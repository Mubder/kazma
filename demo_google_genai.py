#!/usr/bin/env python
"""Demo script showcasing standard text generation vs. Gemini Code Assist.

Before running, ensure you are authenticated:
- For AI Studio: export GEMINI_API_KEY="your-key"
- For Vertex AI: gcloud auth application-default login
"""

from __future__ import annotations

import os
import sys

# Ensure kazma_core is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "kazma-core"))

from kazma_core.google_genai_provider import (
    initialize_google_provider,
    generate_text,
    generate_code,
    GoogleProviderError,
)


def run_demo() -> None:
    # ── 1. Create a configuration dictionary (simulate DB load) ──
    # Switch google_mode between "ai_studio" and "vertex_ai" to test each path
    mode = "ai_studio" if os.getenv("GEMINI_API_KEY") else "vertex_ai"
    
    config_dict = {
        "google_mode": mode,
        "api_key": os.getenv("GEMINI_API_KEY"),
        "project_id": os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT"),
        "location": "us-central1",
    }

    print("=" * 60)
    print(f"Initializing Unified Client in {mode.upper()} mode...")
    print("=" * 60)

    try:
        # ── 2. Initialize the client ──
        client = initialize_google_provider(config_dict)
        print("Success: Google Client Initialized successfully!\n")
    except GoogleProviderError as err:
        print(f"Initialization Failed: {err}")
        print("To run this demo:")
        print("  - For AI Studio: set GEMINI_API_KEY environment variable")
        print("  - For Vertex AI: run 'gcloud auth application-default login'")
        return

    # ── 3. Standard Text Generation Demo ──
    text_prompt = "Explain quantum computing in one short sentence."
    print("-" * 60)
    print(f"Executing standard generate_text() call...")
    print(f"Prompt: {text_prompt}")
    print("-" * 60)

    try:
        reply_text = generate_text(client, text_prompt)
        print(f"Response:\n{reply_text}\n")
    except GoogleProviderError as err:
        print(f"Text Generation failed: {err}\n")

    # ── 4. Gemini Code Assist (with internal Code Execution sandbox) Demo ──
    code_prompt = (
        "Write a Python function to calculate the first 10 Fibonacci numbers, "
        "execute the function, and print the output."
    )
    print("-" * 60)
    print(f"Executing generate_code() (Gemini Code Assist) call...")
    print(f"Prompt: {code_prompt}")
    print("-" * 60)

    try:
        reply_code = generate_code(client, code_prompt)
        print(f"Response:\n{reply_code}\n")
    except GoogleProviderError as err:
        print(f"Code Assist failed: {err}\n")


if __name__ == "__main__":
    run_demo()
