---
id: document-security
title: Document Security
sidebar_label: Document Security
description: Security architecture and threat model for the Kazma document intelligence platform.
---

# Document Security

The document intelligence platform is designed with the assumption that every
document is hostile until proven otherwise. This page documents the security
architecture, threat model, and defense-in-depth measures.

**Related:** [Document Intelligence](../guide/document-intelligence.md) ·
[Document processing ops](../ops/document-processing.md) ·
[Phase map](../guide/document-phases.md)

---

## Threat model

### Attacker capabilities (assumed)

- Can upload crafted documents (PDF, DOCX, XLSX, PPTX, images, plain text, CSV, HTML, RTF).
- Can embed malicious content: prompt injection, XXE, macros, active content, polyglot files, compression bombs, archive bombs, corrupt/malformed structures.
- Can attempt path traversal through filename or embedded references.
- Can attempt SSRF through external OOXML relationships.
- Can attempt redaction bypass.

### Attacker capabilities (out of scope / mitigated externally)

- Filesystem access (workspace root sandboxing for import paths).
- Full OS-level network isolation of parser processes (see Layer 3 honesty notes).
- GPU/memory side-channels beyond the resource limits enforced by the OS.

---

## Defense layers

### Layer 1: Intake gate

- **Streamed limits:** document platform defaults to 50 MiB per file / 10 files
  per request (`documents.intake.*`); chat gateway attachments use 20 MiB per
  file / 50 MiB aggregate. Limits are enforced while streaming, before unbounded
  buffering.
- **Workspace containment (import path):** workspace-safe local imports resolve
  through `workspace.binding.resolve_active_root()`; `..` path traversal refused.
- **Extension/content mismatch:** MIME sniffing rejects mismatched file
  extensions before any parser sees the content.

### Layer 2: Content sniffing

Before any parser or renderer is invoked, the content sniffing layer checks:

| Check | Defense |
|---|---|
| OOXML structural analysis | Rejects XXE (`<!DOCTYPE`, `<!ENTITY`), external relationships (`TargetMode="External"`), macro payloads (`vbaProject.bin`), nested archives, compression bombs (expansion ratio > configurable budget) |
| PDF policy | Rejects encrypted PDFs, active content (JavaScript, launch actions, embedded files), polyglot (PDF/ZIP) files |
| UTF-8 validity | Rejects invalid byte sequences without attempting lossy decode |
| Content-type validation | Rejects binaries claiming to be text, HTML claiming to be plain |
| Archive member count | Enforces configurable max archive members |

### Layer 3: Isolated subprocess execution

Every parse, OCR, render, and mutation job runs in a host subprocess:

- `python -I` (isolated mode — no user site packages).
- **Scrubbed environment** (parent secrets and unsafe env vars removed).
- Memory limit enforced by OS where available (Windows Job Objects, Unix `setrlimit`).
- Configurable timeout — process killed, not leaked.
- Stdout/stderr bounded; output size limited.
- On timeout/OOM/silent exit: **fail closed** — no partial data escapes.

**Honesty note — network:** isolation is **not** a full network namespace /
firewall jail. Defense is “scrubbed env + no intentional network clients in
parser workers + fail-closed on missing output.” Do not treat this as
air-gapped execution.

**Malware scan (ClamAV, pluggable):** Intake runs
`kazma_core.documents.malware.scan_if_configured` on quarantined bytes.

| Mode (`documents.security.malware_scan`) | Behavior |
|---|---|
| `off` | Never scan |
| `auto` (default) | Scan when `clamscan` or `clamdscan` is on PATH; otherwise skip (unless fail-closed) |
| `on` | Require a scanner; missing/broken scanner fails closed |

`documents.security.malware_fail_closed=true` treats scanner missing/errors as
hard rejects even in `auto`. Infected files raise `malware_detected`. Install
ClamAV system packages separately (not a pip dependency).

### Layer 4: Prompt fencing

Platform durable/transient reads that surface document body text to the model
use an untrusted-data fence, for example:

```
<kazma:data source="document" untrusted="true">
[document content]
</kazma:data>
```

Chat **attachment** auto-excerpts may use `source="document_attachment"` via
`format_untrusted_block()` after a best-effort `DocumentService` parse.

This fence tells the model that the content is observation data, not
instructions — mitigating prompt injection from document text.

Self-improvement prompt deltas go through the same fence machinery via
`format_untrusted_block()` with the `prompt_fence` safety check.

### Layer 5: Redaction verification

The redaction pipeline:

1. Uses a verified mutation/redaction path (optional engines required for some PDF modes).
2. Applies conservative physical/raster redaction (not insecure overlay-only boxes).
3. Verifies post-redaction integrity checks (including size/structure expectations).
4. Produces a **new immutable artifact** — originals are not silently rewritten.

**Confirmation model:**

| Path | Interactive confirm? |
|---|---|
| Web UI Documents page | Yes — `kazmaPrompt` / `kazmaConfirm` before `POST …/redact` |
| REST API / agent `document_redact` / gateway `/documents redact` | No UI dialog — caller is trusted as an authenticated actor; ACL still applies |

Insecure overlay redaction (opaque boxes over text without removing underlying
content) was identified in pre-platform code and is **not** the platform path.

### Layer 6: Audit immutability

`document_audit_events` has UPDATE/DELETE triggers that **reject** any
modification after insert (retention sweep uses a guarded control path). The
audit trail is append-only and tenant-scoped. Details are allowlisted to safe
scalars — document content, filenames, redaction terms, and secrets never enter
the audit.

### Layer 7: Storage integrity

- **Content-addressed storage:** Every blob is identified by SHA-256.
  Physical path: `{root}/{kind}/sha256/{aa}/{bb}/{full_hash}`.
- **Hash verification on promotion:** Bytes verified before quarantine →
  originals promotion.
- **Dedup:** Same hash = same bytes — physical deduplication without
  additional verification passes.
- **Symlink/junction refusal:** GC, backup, and migration refuse to follow
  or delete through links.

---

## Hostile corpus certification

Every release runs a **deterministic, programmatically generated** hostile
corpus (19 cases) covering:

| Category | Cases |
|---|---|
| Archive bombs | Compression bomb, nested archive, member flood |
| XML attacks | XXE, external OOXML relationships |
| Active content | Macros, PDF JavaScript/launch actions |
| Encryption | PDF with /Encrypt declarations |
| Polyglot | PDF/ZIP combined file |
| Corruption | Truncated OOXML, malformed cross-reference, invalid UTF-8 |
| Limit attacks | Page, pixel, cell count exceeds budgets |
| Prompt injection | Instruction-like text in documents |
| Unicode/BiDi | Arabic-English with confusable extensions and bidi controls |
| Parser crash | Synthetic crash containment test |

Every case has:

- A reviewed description.
- Expected disposition (reject / fenced).
- Expected error codes.
- Deterministic SHA-256 hash.

The committed manifest (`tests/fixtures/documents/hostile_manifest.json`)
is verified at certification time — if the generated corpus differs, release
is blocked.

Run: `python scripts/certify_documents.py`

---

## Rollback safety

| Action | Safe? | Detail |
|---|---|---|
| Set `documents.enabled=false` | ✅ | Stops new durable writes; existing blobs, jobs, manifests, metadata preserved |
| Set `documents.shadow=true` | ✅ | Shadow/canary posture; no data loss on toggle |
| Set `documents.default_authoritative=true` | ✅ | Routes product path to platform; rollback preserves data |
| Delete `documents.db` | ❌ | Destroys metadata, job state, audit trail — backup first |
| Delete content store directory | ❌ | Destroys all blobs — irrecoverable without backup |

---

## Recommendations for production

1. **Install ClamAV** (`clamscan`/`clamdscan` on PATH) for production; verify
   readiness `malware.available` and consider `security_malware_scan=on` +
   fail-closed.
2. **Set `documents.capacity.storage_free_floor_bytes` conservatively** (default
   is 512 MiB; many operators prefer ≥ 1 GiB).
3. **Review `documents.retention.*` / `documents.gc.*`** for compliance.
4. **Run `scripts/certify_documents.py --soak`** before first production deploy.
5. **Monitor** `GET /api/documents/ops/capacity` for degraded reasons.
6. **Audit** `GET /api/documents/ops/audit` periodically.
7. **Backup** the nightly document backup alongside your primary backup.
8. **Treat redaction via API/tools as privileged** — UI confirm is not a
   server-side gate on those paths.
