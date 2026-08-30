# Pinned document fonts

Fonts here pin the typography of generated documents so the same input produces
the same output on Windows, macOS and the container.
`kazma_core.documents.fonts` searches this directory, verifies that a face
actually covers the Arabic **presentation forms** the reshaper emits
(`U+FB50–FDFF` / `U+FE70–FEFF`), and only then accepts it.

## What ships here

| File | Role | Licence |
|------|------|---------|
| `Amiri-Regular.ttf` | Naskh body text for complex-script PDF rendering | SIL OFL 1.1 (`OFL.txt`) |
| `Amiri-Bold.ttf` | Bold companion | SIL OFL 1.1 |

Amiri 1.003, from the upstream release at
<https://github.com/aliftype/amiri/releases/tag/1.003>.

## Precedence — deliberately asymmetric

- **Complex-script jobs** take these fonts first. They are verified for shaped
  Arabic and they are the only way to get identical output across hosts.
- **Latin-only jobs** take the *system* font first, and only fall back here when
  no system font exists at all. Amiri is a Naskh design; letting it win for
  Latin would silently restyle every English document that renders in Calibri
  today. Pinning Arabic typography must not change Latin typography.

## Overriding

`KAZMA_DOCUMENT_FONT_DIR` points at a different directory. Regular/Bold pairs
are matched by filename stem, so keep upstream names. Noto Naskh Arabic (also
OFL) is a good alternative if you prefer a screen-first face.

## What this does NOT do

These fonts are used by the **PDF** path. DOCX still names
`THEME["font_arabic"]` (Sakkal Majalla) and does not embed a font — switching
the DOCX typeface would mean re-tuning `body_size_ar` / `line_height_ar`, which
are calibrated to Sakkal Majalla's optical size. That is a design decision, not
a packaging one.
