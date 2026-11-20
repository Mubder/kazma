"""Make every image of Kazma's logo from its master (2026-11-20).

The master is ``static/img/brand/kazma-logo-master.webp``: the blue and white
K on transparent, 2000 px square (the owner's logo of 2026-11-20). Everything
is written from it, so the logo is changed in one place. The logo has two
faces, one per theme (the pages show the one for theirs):

- the dark-theme mark is the artwork as drawn: white strokes and blue ones;
- the light-theme mark keeps the blue strokes as drawn and turns the white
  strokes into a deep navy ink, shaded as they were (``light_mark``). White
  strokes vanish on a light page; recolouring every stroke blue (the
  website's first try) lost the two tones and read as one solid blue K.

Written:

- ``kazma-logo.png`` / ``kazma-logo-dark-theme.png`` (cropped to the K's own
  edges, 512 px high): the sidebar, the docs site
  (``docs/static/img/kazma-logo*.png``) and the README;
- ``kazma-icon.png`` / ``kazma-icon-dark-theme.png`` (256 px high): the chat's
  avatar and welcome, the login page, the notification badge. The plain names
  are the light-theme mark: it is the one that reads on a page of either
  colour, so a place that shows one image uses it;
- ``favicon.png`` (64 px) and ``favicon.ico`` (16, 32, 48): the artwork on
  the dark rounded square, which reads on a light or a dark tab bar;
- ``app-icon-192.png`` / ``app-icon-512.png``: the artwork on a dark rounded
  square (purpose ``any`` in the manifest; also the notification icon);
- ``app-icon-maskable-512.png``: a full dark square, the artwork inside the
  middle 80 % a launcher may crop to a circle (purpose ``maskable``);
- ``apple-touch-icon.png`` (180): a full dark square (iOS rounds it itself
  and fills any transparency with black).

    .venv/Scripts/python.exe scripts/make_app_icons.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "kazma-ui" / "kazma_ui" / "static" / "img"
MASTER = IMG / "brand" / "kazma-logo-master.webp"
DOCS_IMG = ROOT / "docs" / "static" / "img"
GROUND = (14, 22, 38, 255)  # #0e1626, the dark theme's wall

# The light-theme mark's ink: the white strokes' shade (dark end) and their
# highlight (light end). Measured on the light page (#f4f7fd) beside the
# navy and slate tries of 2026-11-20: this one keeps the blue strokes
# standing apart from the ink at the sidebar's 32 px.
INK_SHADE = (10, 22, 52)
INK_HIGHLIGHT = (36, 66, 128)


def _crop(logo: Image.Image) -> Image.Image:
    """The logo cropped to the K's own edges (its glow kept)."""
    box = logo.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return logo.crop(box) if box else logo


def dark_mark() -> Image.Image:
    """The artwork as drawn, cropped."""
    return _crop(Image.open(MASTER).convert("RGBA"))


def light_mark() -> Image.Image:
    """The blue strokes as drawn, the white strokes in navy ink, cropped.

    A pixel's saturation says which stroke it belongs to (the white strokes
    are a faintly blue grey, the blue ones saturated), blended over a short
    band so the edge between them stays soft; the white strokes' brightness
    becomes the ink's shade, so their highlights stay where they were.
    """
    art = np.asarray(Image.open(MASTER).convert("RGBA")).astype(np.float32) / 255.0
    rgb, alpha = art[..., :3], art[..., 3:]
    top = rgb.max(-1, keepdims=True)
    low = rgb.min(-1, keepdims=True)
    saturation = np.where(top > 0, (top - low) / np.maximum(top, 1e-6), 0.0)
    blue = np.clip((saturation - 0.16) / 0.20, 0.0, 1.0)
    shade = np.clip((top - 0.55) / 0.45, 0.0, 1.0)
    dark = np.array(INK_SHADE, np.float32) / 255.0
    light = np.array(INK_HIGHLIGHT, np.float32) / 255.0
    ink = dark + (light - dark) * shade
    out = np.concatenate([rgb * blue + ink * (1.0 - blue), alpha], -1)
    return _crop(Image.fromarray((out * 255.0).round().astype(np.uint8), "RGBA"))


def _height(logo: Image.Image, height: int) -> Image.Image:
    width = round(logo.width * height / logo.height)
    return logo.resize((width, height), Image.LANCZOS)


def _icon(art: Image.Image, size: int, logo_share: float, *, rounded: bool) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ground = Image.new("RGBA", (size, size), GROUND)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    if rounded:
        draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=round(size * 0.22), fill=255)
    else:
        draw.rectangle((0, 0, size - 1, size - 1), fill=255)
    canvas.paste(ground, (0, 0), mask)
    logo = _height(art, round(size * logo_share))
    canvas.alpha_composite(logo, ((size - logo.width) // 2, (size - logo.height) // 2))
    return canvas


def main() -> None:
    art, light = dark_mark(), light_mark()
    for name, mark in (("kazma-logo.png", light), ("kazma-logo-dark-theme.png", art)):
        logo = _height(mark, 512)
        logo.save(IMG / name, optimize=True)
        logo.save(DOCS_IMG / name, optimize=True)
    _height(light, 256).save(IMG / "kazma-icon.png", optimize=True)
    _height(art, 256).save(IMG / "kazma-icon-dark-theme.png", optimize=True)
    _icon(art, 64, 0.74, rounded=True).save(IMG / "favicon.png", optimize=True)
    _icon(art, 256, 0.74, rounded=True).save(IMG / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    _icon(art, 192, 0.70, rounded=True).save(IMG / "app-icon-192.png", optimize=True)
    _icon(art, 512, 0.70, rounded=True).save(IMG / "app-icon-512.png", optimize=True)
    _icon(art, 512, 0.56, rounded=False).save(IMG / "app-icon-maskable-512.png", optimize=True)
    _icon(art, 180, 0.70, rounded=False).convert("RGB").save(IMG / "apple-touch-icon.png", optimize=True)
    print("wrote the logo's images into", IMG, "and", DOCS_IMG)


if __name__ == "__main__":
    main()
