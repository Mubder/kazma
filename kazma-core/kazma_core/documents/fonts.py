"""Font resolution for visual document engines — chosen by coverage, not by luck.

The previous policy was ``next(pair for pair in candidates if pair[0].is_file())``
under a docstring promising "fonts with solid Arabic coverage". First path that
exists won, with no coverage check at all, which meant:

* on Linux ``DejaVuSans.ttf`` was picked ahead of ``NotoSansArabic-Regular.ttf``
  even when both were installed;
* ``LiberationSans`` — no Arabic whatsoever — was a live candidate;
* in the shipped container **no** candidate existed, so ReportLab silently fell
  back to Helvetica (a Type-1 base font with zero Arabic glyphs) and emitted a
  warning string into a list nobody reads;
* the same input produced different PDFs on different machines.

Two things fix that. First, a candidate is only accepted for an RTL job when its
``cmap`` actually covers the Arabic **presentation forms** — the reshaper emits
``U+FB50–FDFF`` / ``U+FE70–FEFF``, so base-block coverage alone still renders
tofu. Second, a bundled font directory is searched first, so a deployment can
pin its typography and get byte-reproducible output.

To pin fonts, drop TTF files into ``kazma_core/documents/assets/fonts/`` (or
point ``KAZMA_DOCUMENT_FONT_DIR`` at a directory). Regular/Bold pairs are
matched by filename. Amiri and Noto Naskh Arabic (both SIL OFL) are the
recommended pair for Arabic print work.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "FontChoice",
    "bundled_font_dir",
    "resolve_fonts",
    "font_paths",
    "has_arabic_coverage",
    "embedded_font_face_css",
]

# Sample codepoints spanning the ranges the reshaper actually emits. A font that
# renders all of these renders shaped Arabic; a font that renders only the base
# block (0600-06FF) does not.
_PRESENTATION_PROBE = (
    0xFE8D,  # ARABIC LETTER ALEF ISOLATED FORM
    0xFEDF,  # ARABIC LETTER LAM INITIAL FORM
    0xFEE0,  # ARABIC LETTER LAM MEDIAL FORM
    0xFBFE,  # ARABIC LETTER FARSI YEH FINAL FORM (Forms-A)
    0xFEFB,  # ARABIC LIGATURE LAM WITH ALEF ISOLATED FORM
)
_BASE_PROBE = (0x0627, 0x0644, 0x0645, 0x064A, 0x0629, 0x0640)

_BUNDLED_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

# Ordered by Arabic typographic quality for the RTL case; the LTR case simply
# takes the first that exists. Each entry is (regular, bold).
_CANDIDATES: tuple[tuple[str, str], ...] = (
    # Linux — dedicated Arabic faces first (this ordering was inverted before).
    ("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
     "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"),
    ("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
     "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf"),
    ("/usr/share/fonts/truetype/amiri/Amiri-Regular.ttf",
     "/usr/share/fonts/truetype/amiri/Amiri-Bold.ttf"),
    # Windows.
    ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("C:/Windows/Fonts/tahoma.ttf", "C:/Windows/Fonts/tahomabd.ttf"),
    ("C:/Windows/Fonts/trado.ttf", "C:/Windows/Fonts/trado.ttf"),
    ("C:/Windows/Fonts/NotoSansArabic-Regular.ttf",
     "C:/Windows/Fonts/NotoSansArabic-Bold.ttf"),
    # macOS.
    ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    # Latin-only fallbacks — never selected for an RTL job.
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
     "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
)

_BOLD_HINTS = ("-bold", "bold", "-bd", "b.ttf")


@dataclass(frozen=True, slots=True)
class FontChoice:
    """A resolved regular/bold pair plus what it can actually render."""

    regular: Path | None
    bold: Path | None
    arabic_ready: bool
    source: str

    @property
    def found(self) -> bool:
        return self.regular is not None


@lru_cache(maxsize=64)
def has_arabic_coverage(path: str) -> bool:
    """True when the font's cmap covers shaped Arabic, not just the base block.

    Cached — resolving fonts happens per render job and parsing a cmap is not
    free. Returns ``False`` (never raises) when the file cannot be parsed.
    """
    try:
        from reportlab.pdfbase.ttfonts import TTFont

        cmap = TTFont("kazma-probe", path).face.charToGlyph
    except Exception:
        logger.debug("[fonts] cmap probe failed for %s", path, exc_info=True)
        return False
    if not all(cp in cmap for cp in _BASE_PROBE):
        return False
    # Tolerate one missing presentation codepoint (fonts differ on the rarer
    # Forms-A ligatures); demand the rest.
    hits = sum(1 for cp in _PRESENTATION_PROBE if cp in cmap)
    return hits >= len(_PRESENTATION_PROBE) - 1


def bundled_font_dir() -> Path | None:
    """The pinned font directory, if a deployment configured or shipped one."""
    override = os.environ.get("KAZMA_DOCUMENT_FONT_DIR", "").strip()
    if override:
        path = Path(override)
        if path.is_dir():
            return path
        logger.warning("[fonts] KAZMA_DOCUMENT_FONT_DIR is not a directory: %s", override)
    if _BUNDLED_DIR.is_dir() and any(_BUNDLED_DIR.glob("*.ttf")):
        return _BUNDLED_DIR
    return None


def _bundled_pair(*, arabic: bool) -> tuple[Path, Path | None] | None:
    directory = bundled_font_dir()
    if directory is None:
        return None
    faces = sorted(directory.glob("*.ttf")) + sorted(directory.glob("*.otf"))
    regulars = [f for f in faces if not any(h in f.name.lower() for h in _BOLD_HINTS)]
    bolds = [f for f in faces if any(h in f.name.lower() for h in _BOLD_HINTS)]
    for regular in regulars or faces:
        if arabic and not has_arabic_coverage(str(regular)):
            continue
        stem = regular.stem.lower().replace("-regular", "").replace("regular", "")
        bold = next((b for b in bolds if stem and stem in b.stem.lower()), None)
        return regular, bold or (bolds[0] if bolds else None)
    return None


def _bundled_choice(*, arabic: bool) -> FontChoice | None:
    pair = _bundled_pair(arabic=arabic)
    if pair is None:
        return None
    regular, bold = pair
    return FontChoice(
        regular=regular,
        bold=bold,
        arabic_ready=has_arabic_coverage(str(regular)),
        source="bundled",
    )


def resolve_fonts(*, arabic: bool) -> FontChoice:
    """Resolve the regular/bold pair for a render job.

    Precedence depends on what the job needs, which is not the same question in
    both directions:

    * **Complex script** — the pinned bundle wins. It is verified for shaped
      Arabic and it is the only way to get identical output on Windows, macOS
      and the container. When *arabic* is True, only faces with verified
      presentation-form coverage are eligible and the caller must treat
      ``arabic_ready=False`` as a hard failure rather than draw blank glyphs.
    * **Latin only** — the system font wins, and the bundle is a last resort.
      The bundled face is an Arabic face (Amiri is a Naskh design); letting it
      take precedence would silently restyle every English document that used
      to render in Calibri. Pinning Arabic typography must not change Latin
      typography.

    The bundle still rescues the Latin case when no system font exists at all —
    a bare pip install with no fonts, where the alternative is ReportLab's
    Helvetica.
    """
    if arabic:
        bundled = _bundled_choice(arabic=True)
        if bundled is not None:
            return bundled

    fallback: FontChoice | None = None
    for regular_str, bold_str in _CANDIDATES:
        regular = Path(regular_str)
        if not regular.is_file():
            continue
        bold = Path(bold_str)
        covered = has_arabic_coverage(str(regular))
        choice = FontChoice(
            regular=regular,
            bold=bold if bold.is_file() else None,
            arabic_ready=covered,
            source="system",
        )
        if not arabic:
            return choice
        if covered:
            return choice
        if fallback is None:
            fallback = choice
    if fallback is not None:
        return fallback

    # No system font at all: the bundle beats Helvetica in either direction.
    bundled = _bundled_choice(arabic=arabic)
    if bundled is not None:
        return bundled
    return FontChoice(regular=None, bold=None, arabic_ready=False, source="none")


def font_paths(*, arabic: bool = False) -> tuple[Path | None, Path | None]:
    """Back-compatible ``(regular, bold)`` accessor used by the PDF engine."""
    choice = resolve_fonts(arabic=arabic)
    return choice.regular, choice.bold


def _embedding_enabled() -> bool:
    """Whether HTML exports may inline the pinned font.

    Inlining makes an export self-contained but adds ~850 KB of base64 to every
    Arabic HTML file. Operators who serve these over the wire rather than
    handing them to someone can turn it off with
    ``documents.render.embed_html_fonts``. Defaults to on, because a document
    that renders in whatever font the reader happens to own is the problem
    pinning exists to solve.
    """
    try:
        from kazma_core.documents.config import get_document_config

        return bool(getattr(get_document_config(), "render_embed_html_fonts", True))
    except Exception:  # pragma: no cover - config unavailable, keep the default
        return True


# Fonts above this size are not inlined — a data URI counts against the page
# and a 4 MB base64 blob in every exported HTML is not a trade worth making.
_MAX_EMBED_BYTES = 2_000_000


def embedded_font_face_css(*, arabic: bool) -> tuple[str, str]:
    """Return ``(css, family_name)`` inlining the pinned font as a data URI.

    An HTML export that only *names* its typeface renders differently on every
    machine that lacks it — different line breaks, different page count, and
    none of the Arabic metrics the theme tunes for. When a font is pinned
    (``documents/assets/fonts/`` or ``KAZMA_DOCUMENT_FONT_DIR``) it is inlined
    so the export is self-contained and travels.

    Returns ``("", "")`` when nothing is pinned, in which case the caller keeps
    its plain ``font-family`` stack — this is a fidelity upgrade, never a
    requirement.
    """
    import base64

    if not _embedding_enabled():
        return "", ""
    if bundled_font_dir() is None:
        return "", ""
    choice = resolve_fonts(arabic=arabic)
    if choice.source != "bundled" or choice.regular is None:
        return "", ""

    family = "KazmaDocEmbedded"
    faces: list[str] = []
    for path, weight in ((choice.regular, 400), (choice.bold, 700)):
        if path is None or not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            logger.debug("[fonts] could not read %s for embedding", path, exc_info=True)
            continue
        if len(data) > _MAX_EMBED_BYTES:
            logger.info(
                "[fonts] %s is %d bytes; too large to inline, skipping",
                path.name,
                len(data),
            )
            continue
        mime = "font/otf" if path.suffix.lower() == ".otf" else "font/ttf"
        encoded = base64.b64encode(data).decode("ascii")
        faces.append(
            "@font-face {\n"
            f"  font-family: '{family}';\n"
            f"  font-weight: {weight};\n"
            "  font-style: normal;\n"
            "  font-display: swap;\n"
            f"  src: url(data:{mime};base64,{encoded}) format('truetype');\n"
            "}"
        )
    if not faces:
        return "", ""
    return "\n".join(faces), family
