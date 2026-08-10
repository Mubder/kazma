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

---

## Threat model

### Attacker capabilities (assumed)

- Can upload crafted documents (PDF, DOCX, XLSX, PPTX, images, plain text, CSV, HTML, RTF).
- Can embed malicious content: prompt injection, XXE, macros, active content, polyglot files, compression bombs, archive bombs, corrupt/malformed structures.
- Can attempt path traversal through filename or embedded references.
- Can attempt SSRF through external OOXML relationships.
- Can attempt redaction bypass.

### Attacker capabilities (out of scope / mitigated externally)

- Filesystem access (workspace root sandboxing).
- Network access from isolated subprocesses (no network in sandbox).
- GPU/memory side-channels beyond the resource limits enforced by the OS.

---

## Defense layers

### Layer 1: Intake gate

- **Streamed limits:** 20 MiB per file, 10 files per request, 50 MiB aggregate.
  Limits are enforced before buffering.
- **Workspace containment:** All write paths resolve through
  `workspace.binding.resolve_active_root()`; `..` path traversal refused.
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

- `python -I` (isolated mode — no user site packages, no parent environment).
- Memory limit enforced by OS (Windows Job Objects, Unix `setrlimit`).
- Configurable timeout — process killed, not leaked.
- No network access (sandboxed imports in parser worker).
- Stdout/stderr bounded; output size limited.
- On timeout/OOM/silent exit: **fail closed** — no partial data escapes.

### Layer 4: Prompt fencing

Every byte of document content that reaches the LLM passes through:

```
<kazma:data source="document" untrusted="true">
[document content]
</kazma:data>
```

This fence tells the model that the content is observation data, not
instructions — mitigating prompt injection from document text.

Self-improvement prompt deltas go through the same fence via
`format_untrusted_block()` with the `prompt_fence` safety check.

### Layer 5: Redaction verification

The redaction pipeline:
1. Renders document pages to images.
2. Applies conservative raster redaction (overwrites at redaction coordinates).
3. Verifies post-redaction byte count ≤ pre-redaction byte count.
4. Requires interactive confirmation (`kazmaPrompt`/`kazmaConfirm`) — no
   unattended redaction.

Insecure overlay redaction (placing opaque boxes over text without removing
the underlying content) was identified in the pre-platform code and **disabled**
— the platform only supports raster redaction.

### Layer 6: Audit immutability

`document_audit_events` has UPDATE/DELETE triggers that **reject** any
modification after insert. The audit trail is append-only and tenant-scoped.
Details are allowlisted to safe scalars — document content, filenames,
redaction terms, and secrets never enter the audit.

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

---

## Rollback safety

| Action | Safe? | Detail |
|---|---|---|
| Set `documents.enabled=false` | ✅ | Stops new durable writes; existing blobs, jobs, manifests, metadata preserved |
| Set `documents.shadow=true` | ✅ | Runs alongside legacy path; no data loss |
| Set `documents.default_authoritative=true` | ✅ | Routes all operations to new platform; rollback preserves data |
| Delete `documents.db` | ❌ | Destroys metadata, job state, audit trail — backup first |
| Delete content store directory | ❌ | Destroys all blobs — irrecoverable without backup |

---

## Recommendations for production

1. **Enable `security_malware_scan`** (ClamAV integration) — defaults to `"auto"`.
2. **Set `capacity_storage_free_floor_bytes`** conservatively (≥ 1 GiB).
3. **Review `retention_*` settings** for your compliance requirements.
4. **Run `scripts/certify_documents.py --soak`** before first production deploy.
5. **Monitor** `GET /api/documents/ops/capacity` for degraded reasons.
6. **Audit** `GET /api/documents/ops/audit` periodically.
7. **Backup** the nightly document backup alongside your primary backup.
