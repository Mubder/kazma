# Pinned document fonts

Fonts here pin the typography of generated documents so the same input produces
the same output on Windows, macOS and the container.
`kazma_core.documents.fonts` searches this directory, verifies that a face
actually covers the Arabic **presentation forms** the reshaper emits
(`U+FB50–FDFF` / `U+FE70–FEFF`), and only then accepts it.

## What ships here

| File | Role | Licence |
|------|------|---------|
| `IBMPlexSansArabic-Regular.ttf` | Brand face (UI + generated docs, EN + AR) | SIL OFL 1.1 (`OFL-IBM-Plex.txt`) |
| `IBMPlexSansArabic-Bold.ttf` | Bold companion | SIL OFL 1.1 |
| `Amiri-Regular.ttf` | Naskh fallback if Plex is removed | SIL OFL 1.1 (`OFL.txt`) |
| `Amiri-Bold.ttf` | Bold companion | SIL OFL 1.1 |

IBM Plex Sans Arabic is preferred when present. Amiri 1.003 remains as a
fallback (`https://github.com/aliftype/amiri/releases/tag/1.003`).

## Precedence

- **IBM Plex** wins for both complex-script and Latin jobs so documents match
  the Kazma UI and the Docusaurus docs.
- **Amiri** is used for Arabic jobs only when Plex is not in the bundle.
- **System fonts** are last (then Helvetica).

## Overriding

`KAZMA_DOCUMENT_FONT_DIR` points at a different directory. Regular/Bold pairs
are matched by filename stem, so keep upstream names.

## DOCX

DOCX names `THEME["font_arabic"]` / `THEME["font_latin"]` (both IBM Plex Sans
Arabic). The generating host and LibreOffice need that family installed (or
the PDF route copies the TTFs into the soffice user profile).
