"""Kazma's mark has a face per theme, and every page shows the theme's (2026-11-20).

The owner's logo is a blue and white K. On the dark theme it is shown as
drawn; on the light theme its white strokes vanish, so the light theme shows
a two-tone mark: the blue strokes as drawn, the white ones in navy ink
(``scripts/make_app_icons.py``). The website's first light mark recoloured
every stroke blue, and the owner asked why the light logo was one solid
blue: the two tones are the design.

Held here:

- every ``<img>`` of the mark in a template is one of a pair, the dark-theme
  face (``brand-on-dark``) beside the light one (``brand-on-light``), and the
  chat's script draws the mark only through ``_kazmaMarkHtml``, which writes
  the pair;
- ``kazma.css`` hides the other theme's face;
- the light mark the script makes keeps the blue strokes as drawn, turns the
  white ones into the navy ink and stands 3:1 on the light page, and the
  shipped images are what the script makes; the website's all-blue
  recolour (made here by its own rule) and the artwork itself are the
  negative controls.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "kazma-ui" / "kazma_ui"
TEMPLATES = UI / "templates"
IMG = UI / "static" / "img"

LIGHT_FILES = {"/static/img/kazma-logo.png", "/static/img/kazma-icon.png"}
DARK_FILES = {"/static/img/kazma-logo-dark-theme.png", "/static/img/kazma-icon-dark-theme.png"}
_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC = re.compile(r'\bsrc="([^"]+)"')
_CLASS = re.compile(r'\bclass="([^"]*)"')


def _mark_imgs(text: str) -> list[tuple[str, set[str]]]:
    found = []
    for tag in _IMG.findall(text):
        src = _SRC.search(tag)
        if not src or src.group(1) not in LIGHT_FILES | DARK_FILES:
            continue
        cls = _CLASS.search(tag)
        found.append((src.group(1), set(cls.group(1).split()) if cls else set()))
    return found


def _pair_problems(text: str) -> list[str]:
    problems = []
    imgs = _mark_imgs(text)
    for src, classes in imgs:
        want = "brand-on-light" if src in LIGHT_FILES else "brand-on-dark"
        if want not in classes:
            problems.append(f"{src} without {want}")
    lights = sum(1 for src, _ in imgs if src in LIGHT_FILES)
    darks = len(imgs) - lights
    if lights != darks:
        problems.append(f"{lights} light faces beside {darks} dark ones")
    return problems


def test_every_template_shows_both_faces():
    problems = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        found = _pair_problems(path.read_text(encoding="utf-8"))
        if found:
            problems[path.relative_to(ROOT).as_posix()] = found
    assert not problems, problems
    sidebar = (TEMPLATES / "components" / "sidebar.html").read_text(encoding="utf-8")
    assert len(_mark_imgs(sidebar)) == 4  # the logo and the agent's avatar


def test_a_lone_mark_is_caught():
    lone = '<img src="/static/img/kazma-icon.png" alt="Kazma" class="welcome-logo">'
    assert _pair_problems(lone)
    unmarked = ('<img src="/static/img/kazma-icon-dark-theme.png" alt="Kazma">'
                '<img src="/static/img/kazma-icon.png" alt="Kazma" class="brand-on-light">')
    assert _pair_problems(unmarked)


def test_the_chat_draws_the_mark_through_its_helper():
    chat = (UI / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    helper = re.search(r"function _kazmaMarkHtml\(.*?\n  \}\n", chat, re.DOTALL)
    assert helper, "chat.js has no _kazmaMarkHtml"
    body = helper.group(0)
    assert "kazma-icon-dark-theme.png" in body and "brand-on-dark" in body
    assert "kazma-icon.png" in body and "brand-on-light" in body
    rest = chat.replace(body, "")
    assert "/static/img/kazma-icon" not in rest, "a mark drawn outside _kazmaMarkHtml"
    assert rest.count("_kazmaMarkHtml(") >= 6  # five welcomes and the avatar


def test_the_stylesheet_hides_the_other_theme_s_face():
    css = (UI / "static" / "css" / "kazma.css").read_text(encoding="utf-8")
    rule = re.search(
        r':root:not\(\[data-theme="light"\]\) \.brand-on-light,\s*'
        r'\[data-theme="light"\] \.brand-on-dark\s*\{\s*display:\s*none !important;',
        css,
    )
    assert rule


def _rgba(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA")).astype(np.float32) / 255.0


def _array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGBA")).astype(np.float32) / 255.0


def _strokes(art: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The artwork's white strokes and its blue ones (opaque pixels)."""
    rgb = art[..., :3]
    top, low = rgb.max(-1), rgb.min(-1)
    sat = np.where(top > 0, (top - low) / np.maximum(top, 1e-6), 0)
    luma = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    opaque = art[..., 3] > 0.6
    return opaque & (sat < 0.15) & (luma > 0.7), opaque & (sat > 0.5)


def _luminance(rgb: np.ndarray) -> float:
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return float(linear @ np.array([0.2126, 0.7152, 0.0722]))


LIGHT_PAGE = np.array([0xF4, 0xF7, 0xFD]) / 255.0  # --bg of the light theme


def _contrast_on_light_page(rgb: np.ndarray) -> float:
    a, b = _luminance(rgb), _luminance(LIGHT_PAGE)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _light_mark_problems(icons, art: np.ndarray, mark: np.ndarray) -> list[str]:
    """What keeps *mark* from being the two-tone light mark of *art*."""
    white, blue = _strokes(art)
    problems = []
    kept = np.abs(mark[..., :3][blue] - art[..., :3][blue]).mean()
    if kept > 0.03:
        problems.append(f"the blue strokes changed by {kept:.3f}")
    low = np.array(icons.INK_SHADE) / 255.0 - 2 / 255.0
    high = np.array(icons.INK_HIGHLIGHT) / 255.0 + 2 / 255.0
    inked = ((mark[..., :3][white] >= low) & (mark[..., :3][white] <= high)).all(-1).mean()
    if inked < 0.99:
        problems.append(f"only {inked:.0%} of the white strokes are navy ink")
    contrast = _contrast_on_light_page(mark[..., :3][white].mean(0))
    if contrast < 3.0:
        problems.append(f"the white strokes stand {contrast:.1f}:1 on the light page")
    return problems


def _website_recolour(art: np.ndarray) -> np.ndarray:
    """The website's first light mark: every low-saturation pixel to blue."""
    rgb = art[..., :3]
    top, low = rgb.max(-1, keepdims=True), rgb.min(-1, keepdims=True)
    sat = np.where(top > 0, (top - low) / np.maximum(top, 1e-6), 0)
    dark, light = np.array([12, 32, 72]) / 255, np.array([40, 100, 232]) / 255
    blue = dark + (light - dark) * top
    out = np.where(sat < 0.3, blue, rgb)
    return np.concatenate([out, art[..., 3:]], -1)


@pytest.fixture(scope="module")
def icons():
    spec = importlib.util.spec_from_file_location("make_app_icons", ROOT / "scripts" / "make_app_icons.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_light_mark_keeps_two_tones(icons):
    art = _array(icons.dark_mark())
    assert not _light_mark_problems(icons, art, _array(icons.light_mark()))


def test_the_shipped_images_are_what_the_script_makes(icons):
    for name, mark in (
        ("kazma-icon.png", icons.light_mark()),
        ("kazma-icon-dark-theme.png", icons.dark_mark()),
    ):
        made = _array(icons._height(mark, 256))
        shipped = _rgba(IMG / name)
        assert shipped.shape == made.shape, name
        assert np.abs(shipped - made).mean() < 2 / 255.0, name


def test_control_an_all_blue_recolour_is_refused(icons):
    art = _array(icons.dark_mark())
    found = _light_mark_problems(icons, art, _website_recolour(art))
    assert any("navy ink" in p for p in found), found


def test_control_the_artwork_itself_is_refused_on_the_light_page(icons):
    art = _array(icons.dark_mark())
    found = _light_mark_problems(icons, art, art)
    assert any("on the light page" in p for p in found), found
