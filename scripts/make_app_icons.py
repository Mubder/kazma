"""Make the web app's icons from Kazma's logo (REMAINING 4.1, 2026-11-14).

The logo (``static/img/kazma-logo.png``) is white and teal on transparent: on
a phone's home screen it needs the brand's dark ground under it. Written:

- ``app-icon-192.png`` / ``app-icon-512.png``: the logo on a dark rounded
  square (purpose ``any`` in the manifest);
- ``app-icon-maskable-512.png``: a full dark square, the logo inside the
  middle 80 % a launcher may crop to a circle (purpose ``maskable``);
- ``apple-touch-icon.png`` (180): a full dark square (iOS rounds it itself
  and fills any transparency with black).

    .venv/Scripts/python.exe scripts/make_app_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

IMG = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "static" / "img"
GROUND = (14, 22, 38, 255)  # #0e1626, the dark theme's wall


def _logo(height: int) -> Image.Image:
    logo = Image.open(IMG / "kazma-logo.png").convert("RGBA")
    width = round(logo.width * height / logo.height)
    return logo.resize((width, height), Image.LANCZOS)


def _icon(size: int, logo_share: float, *, rounded: bool) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ground = Image.new("RGBA", (size, size), GROUND)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    if rounded:
        draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=round(size * 0.22), fill=255)
    else:
        draw.rectangle((0, 0, size - 1, size - 1), fill=255)
    canvas.paste(ground, (0, 0), mask)
    logo = _logo(round(size * logo_share))
    canvas.alpha_composite(logo, ((size - logo.width) // 2, (size - logo.height) // 2))
    return canvas


def main() -> None:
    _icon(192, 0.70, rounded=True).save(IMG / "app-icon-192.png", optimize=True)
    _icon(512, 0.70, rounded=True).save(IMG / "app-icon-512.png", optimize=True)
    _icon(512, 0.56, rounded=False).save(IMG / "app-icon-maskable-512.png", optimize=True)
    _icon(180, 0.70, rounded=False).convert("RGB").save(IMG / "apple-touch-icon.png", optimize=True)
    print("wrote the app icons into", IMG)


if __name__ == "__main__":
    main()
