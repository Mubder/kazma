# Supply chain — verifying a Kazma release

Kazma is self-hosted. You run it on your own box, often against your own
vault, and it is allowed to touch your files, your shell and your repo. A
release you cannot verify is a release you are taking on trust, and "trust me"
is the one thing this project is not willing to ask for.

So every release artifact is **signed**, carries **build provenance**, and
ships an **SBOM**. This page is how you check all three. None of it requires
an account, a vendor, or a paid service.

---

## What a release contains

| File | What it is |
|------|------------|
| `kazma-<version>-py3-none-any.whl` | The wheel |
| `kazma-<version>.tar.gz` | The source distribution |
| `kazma-<version>.cdx.json` | CycloneDX 1.6 SBOM of the locked dependency set |
| `SHA256SUMS` | Checksums for the three files above |
| `*.sigstore.json` | A Sigstore bundle per artifact — the signature |

Provenance attestations are not files in the release; GitHub stores them and
`gh attestation verify` fetches them for you.

---

## 1. Provenance — was this built from this repo?

This is the check that matters most, and it is one command:

```bash
gh attestation verify kazma-0.11.0-py3-none-any.whl --repo Mubder/kazma
```

A pass means GitHub's transparency log agrees the file was produced by the
`Release` workflow in `Mubder/kazma`, from a specific commit, in a run you can
go and read. A file someone rebuilt, patched, or swapped in fails here — it
cannot be re-attested without write access to this repository.

---

## 2. Signature — Sigstore, keyless

The signing is **keyless**: there is no private key held by the maintainer, so
there is no key to leak, rotate, or lose. The identity being certified is the
GitHub Actions workflow itself, and the certificate is recorded in Sigstore's
public transparency log.

```bash
pip install sigstore

python -m sigstore verify github \
  --repository Mubder/kazma \
  --bundle kazma-0.11.0-py3-none-any.whl.sigstore.json \
  kazma-0.11.0-py3-none-any.whl
```

The explicit form, if you want to pin the exact identity rather than let the
`github` subcommand build it:

```bash
python -m sigstore verify identity \
  --cert-identity "https://github.com/Mubder/kazma/.github/workflows/release.yml@refs/tags/v0.11.0" \
  --cert-oidc-issuer "https://token.actions.githubusercontent.com" \
  --bundle kazma-0.11.0-py3-none-any.whl.sigstore.json \
  kazma-0.11.0-py3-none-any.whl
```

The `@refs/...` suffix is the git ref the release was built from. Check the
run if you are unsure which one applies.

---

## 3. Checksums

```bash
sha256sum -c SHA256SUMS
```

Weakest of the three on its own — a checksum file can be replaced alongside
the artifacts. It is worth running because `SHA256SUMS` is *also* signed, so
verifying its Sigstore bundle first makes the checksums meaningful.

---

## 4. The SBOM — what is actually in it

`kazma-<version>.cdx.json` is CycloneDX 1.6, generated from `uv.lock` rather
than from the build machine's environment. That distinction matters: it
describes the exact pinned set that `uv sync` installs for you, not whatever
the CI runner happened to resolve on the day.

Feed it to whatever your organisation already uses. For a quick look:

```bash
# Component count and licences
python - <<'PY'
import json
d = json.load(open("kazma-0.11.0.cdx.json", encoding="utf-8"))
comps = d.get("components") or []
print(f"{len(comps)} components, CycloneDX {d['specVersion']}")
PY

# Known vulnerabilities, via any CycloneDX-aware scanner
grype sbom:kazma-0.11.0.cdx.json
trivy sbom kazma-0.11.0.cdx.json
```

---

## Rebuilding what is vendored

Two third-party assets are committed rather than fetched, because the UI must
render on a box with no route to the internet:

- **CodeMirror 5** (MIT) — `kazma-ui/kazma_ui/static/vendor/codemirror/`.
  Rebuild with `python scripts/vendor_codemirror.py`; the pinned version lives
  in that script and in the `VERSION` file beside the bundle.
- **IBM Plex Sans Arabic** (OFL) — `kazma-ui/kazma_ui/static/fonts/`.

`tests/test_editor_assets_offline.py` fails the build if any template starts
loading remote script or stylesheets again.

---

## What this does not prove

Worth saying plainly, because a supply-chain page that oversells is worse than
none at all.

- **Not reproducible builds.** Provenance proves *who* built the artifact and
  from which commit. It does not let you rebuild it independently and get a
  byte-identical result. That is a larger piece of work and is not claimed.
- **Provenance is not a code review.** It proves the artifact came from this
  repository. It says nothing about whether the code in that repository is
  good, safe, or free of bugs. Read it; that is why it is open.
- **The SBOM lists dependencies, not vulnerabilities.** It is an input to a
  scanner, not a clean bill of health.
- **Sigstore's log is public.** Verifying leaks nothing, but the fact that a
  release exists is public information by design.
