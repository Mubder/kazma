"""The owner's accent colour, as the two theme variants the stylesheet reads.

Settings > Appearance stores one hex. The stylesheet derives every accent
shade from ``--accent`` per theme (kazma.css §1 and §23), and ``--accent``
reads ``--accent-chosen-dark`` / ``--accent-chosen-light`` when they are set
on ``<html>``. This module works those out the way
``static/js/modules/components.js`` (``accentVariants``) does (``_variants`` here), so the server
can render them on ``<html>`` before the first paint (no default-blue
flash) and the page script can apply a new choice without a reload. The two
are held equal by ``tests/fixtures/accent_variants.json``
(``tests/test_ui_color_tokens.py`` and ``tests/js/test_accent_family.js``).

The rules:

- dark: the chosen colour, lightened toward white in 5% steps until it
  stands 3:1 off the dark wall (it is a fill, a ring and the base of the
  text shade ``--accent-light``) and white or the near-black ink reads
  4.5:1 on it (a mid grey reads 4.48:1 with either); its ink is the one
  that reads better.
- light: the chosen colour, darkened toward Kazma's navy in 5% steps until
  white ink on it reaches 4.5:1 (which also makes it readable as text on
  the white wall); its ink is white.

An empty or malformed value means the owner chose nothing: no variables are
set and each theme keeps its own accent.
"""

from __future__ import annotations

import math
import re

__all__ = ["accent_style"]

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
DARK_WALL = (0x0E, 0x16, 0x26)
NAVY = (0x0C, 0x15, 0x26)
WHITE = (255, 255, 255)
DARK_INK = "#040815"
LIGHT_INK = "#ffffff"
_DARK_INK_RGB = (0x04, 0x08, 0x15)


def _rgb(hex_value: str) -> tuple[int, int, int]:
    return int(hex_value[1:3], 16), int(hex_value[3:5], 16), int(hex_value[5:7], 16)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{c:02x}" for c in rgb)


def _channel(c: int) -> float:
    s = c / 255
    return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    # floor(x + 0.5): the same rounding as JavaScript's Math.round here
    # (Python's round() rounds halves to even).
    return tuple(int(math.floor(x + (y - x) * t + 0.5)) for x, y in zip(a, b))  # type: ignore[return-value]


def _best_ink(rgb: tuple[int, int, int]) -> float:
    return max(_contrast(rgb, WHITE), _contrast(rgb, _DARK_INK_RGB))


def _variants(hex_value: object) -> dict[str, str] | None:
    """The custom properties for a chosen accent, or None when none is chosen."""
    if not isinstance(hex_value, str) or not _HEX.match(hex_value.strip()):
        return None
    base = _rgb(hex_value.strip().lower())

    dark = base
    for step in range(1, 21):
        if _contrast(dark, DARK_WALL) >= 3.0 and _best_ink(dark) >= 4.5:
            break
        dark = _mix(base, WHITE, step / 20)
    dark_on = LIGHT_INK if _contrast(dark, WHITE) >= _contrast(dark, _DARK_INK_RGB) else DARK_INK

    light = base
    for step in range(1, 21):
        if _contrast(light, WHITE) >= 4.5:
            break
        light = _mix(base, NAVY, step / 20)

    return {
        "--accent-chosen-dark": _hex(dark),
        "--accent-chosen-dark-rgb": "{}, {}, {}".format(*dark),
        "--accent-chosen-dark-on": dark_on,
        "--accent-chosen-light": _hex(light),
        "--accent-chosen-light-rgb": "{}, {}, {}".format(*light),
        "--accent-chosen-light-on": LIGHT_INK,
    }


def accent_style(hex_value: object) -> str:
    """The inline ``style`` text for ``<html>``; empty when none is chosen."""
    props = _variants(hex_value)
    if not props:
        return ""
    return ";".join(f"{k}:{v}" for k, v in props.items())
