"""Retake the screenshots on kazma.ai, in both languages and both themes.

    python scripts/site_screenshots.py --site ../KazmaAI

Runs tests/site/test_site_screenshots.py once per language and theme -- each
on the test harness: a private data directory, demo data, a scripted model,
never a live install -- then writes the website's files,
public/screenshots/<view>-<lang>[-light].webp at 2160x1350. The site shows a
view's dark capture in its dark theme and the -light one in its light theme
(src/components/ui/BrowserFrame.astro).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VIEWS = ("chat-approval", "chat-answer", "ide", "connectors", "memory")
SIZE = (2160, 1350)


def _capture(shots_dir: str, lang: str, theme: str) -> int:
    env = dict(
        os.environ,
        KAZMA_SITE_SHOTS_DIR=shots_dir,
        SHOT_LANG=lang,
        SHOT_THEME=theme,
        OMP_NUM_THREADS="2",
        MKL_NUM_THREADS="2",
        PYTHONIOENCODING="utf-8",
    )
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/site/test_site_screenshots.py",
         "-q", "-p", "no:cacheprovider", "-W", "ignore"],
        cwd=REPO,
        env=env,
        check=False,
    )
    return run.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retake the screenshots on kazma.ai.")
    parser.add_argument("--site", required=True, help="the KazmaAI checkout")
    parser.add_argument("--langs", default="en,ar", help="comma-separated (default: en,ar)")
    parser.add_argument("--themes", default="dark,light", help="comma-separated (default: dark,light)")
    args = parser.parse_args(argv)

    out_dir = Path(args.site).resolve() / "public" / "screenshots"
    if not out_dir.is_dir():
        print(f"not a KazmaAI checkout: {out_dir} is missing", file=sys.stderr)
        return 2
    from PIL import Image

    runs = [(lang, theme) for lang in args.langs.split(",") for theme in args.themes.split(",")]
    with tempfile.TemporaryDirectory(prefix="kazma-site-shots-") as tmp:
        # Every capture first: the website's files change only when all of
        # them succeeded.
        for lang, theme in runs:
            print(f"capturing {lang} / {theme} ...", flush=True)
            code = _capture(tmp, lang, theme)
            if code != 0:
                print(f"capture failed: {lang} / {theme}; the website's files are unchanged",
                      file=sys.stderr)
                return code
        for lang, theme in runs:
            suffix = "" if theme == "dark" else f"-{theme}"
            for view in VIEWS:
                source = Path(tmp) / f"{view}-{lang}{suffix}.png"
                target = out_dir / f"{view}-{lang}{suffix}.webp"
                image = Image.open(source).convert("RGB").resize(SIZE, Image.LANCZOS)
                image.save(target, "WEBP", quality=82, method=6)
                print(f"  {target.name}  {target.stat().st_size // 1024} KB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
