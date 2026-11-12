# Supply chain — verifying a Kazma release

> This page is published at [kazma.ai/docs/security/supply-chain/](https://kazma.ai/docs/security/supply-chain/) ([Arabic](https://kazma.ai/ar/docs/security/supply-chain/)).

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

## Kazma is not on PyPI

Kazma ships through the GitHub releases of `Mubder/kazma` and the repository
itself, nowhere else. On PyPI the project holds `kazma`, `kazma-core` and the
names around them with reservations: version 0.0.1 of each, with no code, only
a page that sends the reader to the GitHub releases. Installing one installs
nothing, and no one else can publish under the name. Never install `kazma` (or
`kazma[...]`) by name from PyPI to get Kazma. Install an extra from your Kazma
folder (`pip install -e ".[web]"`, or Settings → Packages) and upgrade with
`kazma update`.

On an install made from a release wheel, `kazma update` reads the newest
release from the GitHub API, downloads its wheel from that release, and
installs it only when the file's SHA-256 matches the release's `SHA256SUMS`
and, where GitHub reports one, GitHub's own digest of the asset, and (since
2026-11) the wheel's Sigstore bundle verifies: chained to Sigstore, recorded
in its transparency log, over the wheel's SHA-256, and naming
`.github/workflows/release.yml` of `Mubder/kazma` run from `main` or a `v*`
tag (`kazma_cli/release_signature.py`). The checksums live in the same release
as the wheel, so whoever could change its files could change them too; the
signature cannot be made without running the release workflow here. A release
from 0.21.2 on without a bundle is not installed. v0.21.1 and older are
checked by their SHA-256 alone, and the command says so; a bundle one of them
does carry is still verified, never ignored. Otherwise it stops and names the
release page. Sigstore's root of trust comes from its TUF repository, read at
each check; without the network the copy on the machine is used, and the
check is never skipped. The Settings update check reads the same release.
Until 2026-09-30 both asked PyPI, and `kazma update` then had pip upgrade
`kazma` by name.

**The next release must be published through `.github/workflows/release.yml`**
(Actions → Release → Run workflow, from `main`). That workflow is what signs
the files and attaches the `.sigstore.json` bundles; a release built and
uploaded by hand has none, and from 0.21.2 on `kazma update` refuses it on
every wheel install. The workflow signed v0.11.0; v0.12.0 to v0.21.1 were
published by hand without bundles, which is why the requirement starts after
them (`release_signature.SIGNED_FROM`). v0.21.2 (2026-11-12) is the first
release published by the workflow since, and its wheel passes the checks below.

### How the names are held {#pypi-names}

The names are `kazma`, `kazma-cli`, `kazma-core`, `kazma-gateway`,
`kazma-memory` (retired), `kazma-skills`, `kazma-tui` and `kazma-ui`
(`scripts/pypi_reserve.py`, which builds the reservations). All eight were
published on 2026-10-01: version 0.0.1 of each, a wheel and a source archive.
The workflow `.github/workflows/pypi-reserve.yml` uploaded them through PyPI
Trusted Publishing, so no PyPI token exists anywhere. It runs only by hand
(Actions → Reserve PyPI names), and without its "publish" box it only builds
and checks the packages.

PyPI accepts the first upload of a name only from a publisher registered for
it, and the publisher then stays with the project. To hold another name, add
it to the script's list (the gate in `tests/test_no_pypi_kazma.py` keeps that
list equal to Kazma's package names), then, on pypi.org, under Account →
Publishing → Add a new pending publisher → GitHub, register it with:

| Field | Value |
|---|---|
| PyPI Project Name | the name (`kazma`, then `kazma-cli`, …) |
| Owner | `Mubder` |
| Repository name | `kazma` |
| Workflow name | `pypi-reserve.yml` |
| Environment name | `pypi-` and the name (`pypi-kazma`, then `pypi-kazma-cli`, …) |

Each name has its own environment because PyPI keeps one configuration
pending for only one new project at a time. With the same environment for
every name, it accepts the first and refuses the rest: "A pending trusted
publisher matching this configuration has already been registered for a
different project name".

Then run the workflow with "publish" ticked and the new name in its "names"
box. Each name is uploaded in its own job, under its own environment, so a
name whose publisher is missing or does not match fails alone
("invalid-publisher"), and the run can be repeated for just that name. An
account holds at most three pending publishers at a time, which is why the
first eight went up in four rounds.

---

## 1. Provenance — was this built from this repo?

This is the check that matters most, and it is one command:

```bash
gh attestation verify kazma-0.21.2-py3-none-any.whl --repo Mubder/kazma
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
  --bundle kazma-0.21.2-py3-none-any.whl.sigstore.json \
  kazma-0.21.2-py3-none-any.whl
```

The explicit form, if you want to pin the exact identity rather than let the
`github` subcommand build it:

```bash
python -m sigstore verify identity \
  --cert-identity "https://github.com/Mubder/kazma/.github/workflows/release.yml@refs/heads/main" \
  --cert-oidc-issuer "https://token.actions.githubusercontent.com" \
  --bundle kazma-0.21.2-py3-none-any.whl.sigstore.json \
  kazma-0.21.2-py3-none-any.whl
```

The `@refs/...` suffix is the git ref the workflow ran from. The release
workflow is started by hand from `main`, so its bundles say
`@refs/heads/main` (v0.21.2's do, as v0.11.0's did); a run from a tag would
say `@refs/tags/v...`. Check the run if you are unsure which one applies.

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
the CI runner happened to resolve on the day. The generator itself is one
release too (`cyclonedx-bom==8.0.1`), as is every tool a workflow or build
script runs through `uvx` or `npx`: with no version they run whatever the
registry serves that day.

Feed it to whatever your organisation already uses. For a quick look:

```bash
# Component count and licences
python - <<'PY'
import json
d = json.load(open("kazma-0.21.2.cdx.json", encoding="utf-8"))
comps = d.get("components") or []
print(f"{len(comps)} components, CycloneDX {d['specVersion']}")
PY

# Known vulnerabilities, via any CycloneDX-aware scanner
grype sbom:kazma-0.21.2.cdx.json
trivy sbom kazma-0.21.2.cdx.json
```

---

## Rebuilding what is vendored

Three third-party assets are committed rather than fetched, because the UI must
render on a box with no route to the internet:

- **CodeMirror 5** (MIT) — `kazma-ui/kazma_ui/static/vendor/codemirror/`.
  Rebuild with `python scripts/vendor_codemirror.py`; the pinned version lives
  in that script and in the `VERSION` file beside the bundle.
- **LiveKit browser client** (`livekit-client` 2.23.0, Apache-2.0) —
  `kazma-ui/kazma_ui/static/vendor/livekit/`, for Live duplex voice. Rebuild
  with `python scripts/vendor_livekit.py`, which checks the tarball against
  the integrity npm published; the version, source, integrity and SHA-256 of
  each file are in `vendored.json` beside it. It was loaded from a CDN,
  unpinned, until 2026-11-11.
- **IBM Plex Sans Arabic** (OFL) — `kazma-ui/kazma_ui/static/fonts/`.

`tests/test_editor_assets_offline.py` fails the build if any template starts
loading remote script or stylesheets again, `tests/test_vendor_livekit.py` if
any page script does, and the pages' Content-Security-Policy refuses a script
from another host in the browser.

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
