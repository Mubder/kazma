"""Each image a generated document shows, decoded and saved again in the worker.

Runs in the renderer worker only (its sandbox or per-job container), never in
the Kazma process: an image is untrusted bytes, and its decoders are the
attack surface. Every image the document tools approved is

  * opened with the four decoders documents accept (PNG, JPEG, GIF, WebP) --
    never Pillow's others: EPS would start Ghostscript;
  * refused past ``MAX_PIXELS`` (a decompression bomb is a small file);
  * turned upright by its EXIF orientation, put in sRGB when it carries a
    colour profile, and reduced to ``MAX_SIDE`` pixels on its longest side;
  * rebuilt from its pixels alone and saved as PNG (anything with
    transparency, or any source but a JPEG) or JPEG -- so no metadata leaves:
    no EXIF (a phone photo's GPS position), no text chunks, no comments, and
    nothing a polyglot file carried after or beside its pixels.

The engines then embed only these cleaned files.
"""

from __future__ import annotations

import io
import logging
import warnings
from dataclasses import dataclass
from pathlib import Path

__all__ = ["ALLOWED_FORMATS", "MAX_PIXELS", "MAX_SIDE", "CleanImage", "ImageRejected", "clean_images"]

#: The decoders a document image may use.
ALLOWED_FORMATS = ("PNG", "JPEG", "GIF", "WEBP")
#: Past this many pixels an image is refused (40 MP: a 7,300 x 5,500 photo).
MAX_PIXELS = 40_000_000
#: The longest side a cleaned image keeps (sharp on a full page at 300 dpi).
MAX_SIDE = 2400
_JPEG_QUALITY = 88

logger = logging.getLogger(__name__)


class ImageRejected(ValueError):
    """An approved image that cannot go into a document, and why."""


@dataclass(frozen=True, slots=True)
class CleanImage:
    """A cleaned image: its file name in the cleaned folder and its size."""

    name: str
    width: int
    height: int


def _has_alpha(img: object) -> bool:
    mode = getattr(img, "mode", "")
    info = getattr(img, "info", {}) or {}
    return mode in ("RGBA", "LA", "PA", "RGBa", "La") or "transparency" in info


def _to_srgb(img: object, icc: bytes | None, mode: str) -> object:
    """*img* converted to sRGB *mode* through its own profile, when it has one."""
    current = getattr(img, "mode", "")
    if current.startswith("I") or current == "F":
        # 16-bit and float greyscale: scaled down to 8 bits first, or Pillow
        # clips every value above 255 to white.
        img = img.convert("I").point(lambda value: value * (1 / 256)).convert("L")  # type: ignore[attr-defined]
    if icc and getattr(img, "mode", "") in ("RGB", "RGBA", "CMYK", "L"):
        try:
            from PIL import ImageCms

            source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            target = ImageCms.createProfile("sRGB")
            converted = ImageCms.profileToProfile(img, source, target, outputMode=mode)
            if converted is not None:
                return converted
        except Exception as exc:  # an odd or hostile profile: the plain conversion below
            logger.debug("[image_clean] colour profile not applied (%s)", type(exc).__name__)
    return img.convert(mode)  # type: ignore[attr-defined]


def _clean_one(source: Path, out_dir: Path) -> CleanImage:
    """Decode *source*, clean it, and save it in *out_dir* under its stem."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    label = source.name
    old_limit = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        with warnings.catch_warnings():
            # Past MAX_IMAGE_PIXELS Pillow only warns (it raises at twice it).
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source, formats=ALLOWED_FORMATS) as probe:
                probe.verify()
            with Image.open(source, formats=ALLOWED_FORMATS) as opened:
                source_format = opened.format
                width, height = opened.size
                if width < 1 or height < 1 or width * height > MAX_PIXELS:
                    raise ImageRejected(f"{label} is {width} x {height} pixels; at most {MAX_PIXELS:,} are accepted")
                opened.seek(0)  # an animated GIF or WebP: its first frame
                opened.load()
                icc = opened.info.get("icc_profile")
                alpha = _has_alpha(opened)
                upright = ImageOps.exif_transpose(opened)
                # Never the opened file itself: it closes with this block.
                img = upright if upright is not None and upright is not opened else opened.copy()
    except ImageRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageRejected(f"{label} has more pixels than a document image may have") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError) as exc:
        raise ImageRejected(f"{label} could not be read as a PNG, JPEG, GIF or WebP image") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = old_limit

    mode = "RGBA" if alpha else "RGB"
    try:
        img = _to_srgb(img, icc if isinstance(icc, bytes) else None, mode)
        if max(img.size) > MAX_SIDE:  # type: ignore[attr-defined]
            img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)  # type: ignore[attr-defined]
        # Rebuilt from its pixels: no info, no EXIF, no profile, nothing else.
        clean = Image.frombytes(mode, img.size, img.tobytes())  # type: ignore[attr-defined]
    except (OSError, ValueError, MemoryError) as exc:
        raise ImageRejected(f"{label} could not be converted") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    as_jpeg = source_format == "JPEG" and not alpha
    target = out_dir / f"{source.stem}.{'jpg' if as_jpeg else 'png'}"
    if as_jpeg:
        clean.save(target, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    else:
        clean.save(target, format="PNG", optimize=False, compress_level=6)
    return CleanImage(name=target.name, width=clean.width, height=clean.height)


def clean_images(source_dir: Path, names: list[str], out_dir: Path) -> dict[str, CleanImage]:
    """Every approved image named in *names*, cleaned: approved name -> cleaned."""
    cleaned: dict[str, CleanImage] = {}
    for name in names:
        cleaned[name] = _clean_one(source_dir / name, out_dir)
    return cleaned
