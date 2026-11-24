<!-- AGENTS.md, piece 19k-documents-egress-images. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**K. Third-party parse egress is opt-in and audited.**
`extract_salvage.try_remote_parse` uploads the ORIGINAL document bytes to
LlamaParse/Reducto. It requires `documents.security.remote_parse` (default
**off**); an API key in the environment is not consent. Local Docling salvage
is `documents.security.local_salvage` (default on), inside the bounded parser
worker, along with structural preflight. Every remote attempt requires a
durable `egress` audit row carrying immutable job identity before upload,
with separate success/failure events; a context-scoped recorder prevents
cross-tenant attribution. Actor, request digest and OCR intent belong to the
job, not a deduplicated version. Consent is rechecked at each provider attempt.
A failed provider is audited (`failure`) and the next configured one is tried,
after the live policy is read again (until 2026-10-13 the first failure
re-raised and the fallback never ran). Both providers use one client
(`_provider_client`): no ambient proxy environment, no redirects (an upload is
never re-sent elsewhere), decoded answers capped at 32 MiB
(`remote_parse_response_limit`). Remote parsing runs in the Kazma process
(`DocumentService`), never in the worker; local salvage runs only in the
worker (every other caller passes `allow_local=False`, gated in
`tests/test_extract_salvage.py`), and a remote parse that is refused or fails
is a WARNING naming the error type (it was DEBUG). Transient excerpts lack durable egress scope and cannot upload remotely. The legacy
`KAZMA_REMOTE_PARSE=0` / `KAZMA_DOCLING=0` env switches remain as an additional
veto only — they can turn a tier off, never on. Gates:
`tests/test_document_egress_hardening.py`, `tests/test_document_egress_pg.py`
(the same rules on real Postgres metadata, jobs, audit and settings),
`tests/test_extract_salvage.py`.

**L. Pictures in generated documents** (2026-11-12, the owner's request).
`generate_pdf` / `generate_docx` place a Markdown image on a line of its own
(`![caption](path)`) where it stands; `generate_pptx` takes a slide's `image`
and `image_caption`, drawn beside the slide's text on the side opposite its
reading start, or across the slide when it has no text. One grammar for both
processes, standard library only: `documents/image_refs.py` (a fenced code
block is text; an image inside a sentence is refused, never dropped).
- **The Kazma process decodes nothing** (`documents/image_inputs.py`,
  `DocumentImages`): an address is refused naming `download_image`; a path is
  read under the file tools' rule (`resolve_in_scope(..., "reads")`) or is in
  Kazma's own `images`/`attachments` folders (absolute, checked on its text
  before it is resolved, strictly); its first bytes must be a PNG, JPEG, GIF
  or WebP (`image_type`, also `vision_analyze`'s sniff); at most `MAX_IMAGES`
  (40: the container takes 64 files a job), `MAX_IMAGE_BYTES`,
  `MAX_TOTAL_BYTES`. Each is copied as `image-NN.<ext>` into a folder of the
  call's own (removed when the call ends) and the reference rewritten, so the
  worker never sees a path; every refusal is `RefusedBeforeEffect`. A Markdown
  file's relative path means its own folder first.
- **The worker cleans before any engine reads** (`documents/image_clean.py`,
  `renderer_worker._cleaned_images`, for generate pdf/docx/html/pptx):
  `Image.open(..., formats=ALLOWED_FORMATS)` only (never EPS: Ghostscript),
  `MAX_PIXELS` with the bomb warning as an error, EXIF orientation applied,
  sRGB through its profile, `MAX_SIDE`, rebuilt from pixels alone (no EXIF,
  GPS, text chunks, comments or trailing payload), saved under `images/`
  beside the output -- never into `assets/`, which `_verify_assets` checks
  again after the render. `ImageRejected` is `WorkerError("invalid_image")`;
  the tool appends the user's file name (`DocumentImages.explain`).
- **Placement** (`renderer_worker._add_body_with_figures`, `_figure_width`:
  6.5 in wide at most, 8 in tall, never above its 96 dpi size): centred, the
  caption under it in its own direction, kept on one page. A picture that was
  approved and cleaned and cannot be embedded fails the document (the engines'
  block loop `DocumentRenderError`); a legacy `images` payload naming no
  approved picture is still skipped.
- **`download_image`** (`tools/image_download.py`, write tier, in
  `FETCH_URL_FIELDS`): one GET through `fetch_public`, judged by its bytes,
  saved under `<workspace>/downloads/images`, never over an earlier file.
Gates: `tests/test_document_images.py` (each hostile file beside what the raw
file carries: GPS, a polyglot, a bomb, an EPS; placement in PDF, Arabic PDF
through LibreOffice, Word and PowerPoint; the tools; the builder without the
split as the negative control), and in the worker image
`test_pictures_are_cleaned_and_placed_inside_the_container`
(`KAZMA_TEST_DOCUMENT_WORKER_IMAGE`).
