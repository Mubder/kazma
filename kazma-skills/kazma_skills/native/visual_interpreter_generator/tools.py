"""Visual Interpreter Generator Native Skill — tools for image analysis and mockup generation."""

from __future__ import annotations

import logging
from pathlib import Path

from kazma_core.tools.vision_analyze import analyze_image
from kazma_core.tools.image_gen import generate_image
from kazma_core.agent.tool_registry import _workspace_scope_error

logger = logging.getLogger(__name__)


async def analyze_local_image(path: str, query: str = "Describe this image in detail.") -> str:
    """Analyze a local screenshot, diagram, or chart and answer visual/structural questions.

    Args:
        path: Path to the image file (or URL).
        query: Specific question or description request about the image.

    Returns:
        Structured textual description of the image content.
    """
    if not path.startswith(("http://", "https://")):
        p = Path(path).expanduser().resolve()
        scope_err = _workspace_scope_error(p, path, "reads")
        if scope_err:
            return scope_err

        if not p.exists():
            return f"Error: Image file not found: {path}"

    try:
        return await analyze_image(path, question=query)
    except Exception as e:
        logger.error("Error analyzing image %s: %s", path, e)
        return f"Error analyzing image: {e}"


async def generate_ui_mockup(prompt: str, aspect_ratio: str = "1:1") -> str:
    """Generate a beautiful wireframe UI design or illustration based on text description prompts.

    Args:
        prompt: Rich textual description of what the design/image should contain.
        aspect_ratio: Image aspect ratio ('1:1', '16:9', '4:3', '9:16').

    Returns:
        Local path to the saved generated image.
    """
    if not prompt or not prompt.strip():
        return "Error: No prompt provided."

    # Map aspect ratio to width/height dimensions
    ratio_map = {
        "1:1": (1024, 1024),
        "16:9": (1280, 720),
        "4:3": (1024, 768),
        "9:16": (720, 1280),
    }

    width, height = ratio_map.get(aspect_ratio, (1024, 1024))

    try:
        img_path = await generate_image(prompt, width=width, height=height)
        return f"Successfully generated design/image.\nSaved path: {img_path}"
    except Exception as e:
        logger.error("Error generating UI mockup: %s", e)
        return f"Error generating UI mockup: {e}"
