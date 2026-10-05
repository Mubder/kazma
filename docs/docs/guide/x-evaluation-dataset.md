---
title: Building an X evaluation dataset
sidebar_position: 18
---

Start with **draft mode**. You can use X Studio with an exact local model,
manual post drafting and attended review before you have a dataset. Keep
`allow_auto` disabled on subject cards while you tune them. Missing evaluation
never grants unattended publication.

Changing Support/Against policy, tone, language, slang or the Allow uncensored
language controls changes evaluation behavior. Include each intended style in
real review cases, including examples where strong wording should still be
held. Re-evaluate the exact saved configuration before enabling unattended
replies; a successful model connection test does not replace this review.

## Collect real examples

Open **Settings → X → Manage Datasets**, or **X Studio → Datasets**.
Create a named collection and choose its purpose: unassigned observations,
tuning, or held-out release evaluation. Purpose stays fixed. **Collect Stored
X Cases** reads retained local observations without calling X or a model.
**Add Real Case** lets you enter original context, quotations, summon identity
and conversation ID. Cases persist across reloads; concurrent edits are rejected
instead of overwriting another reviewer. Each collection supports 1,000 cases
and 2 MB, with up to 100 collections per tenant.

The editor exposes language, failure categories, expected target, eligibility,
evidence and safety labels, rationale and review notes. Unlabeled values stay
unlabeled. Mark source completeness only after checking the original. A review
attestation belongs to the signed-in human reviewer, and editing or importing a
case clears it. Conversations used in a tuning collection remain excluded from
held-out report export, even after their tuning cases are archived. Related
cases count as one conversation; collect independent cases for release.

Create two separate collections: a tuning set for improving policies/prompts,
and a held-out release set that you do not use to tune them. Keep posts from
the same conversation in one collection; paraphrases and repeated posts are
not independent cases. Record the original post ID, exact text, author,
conversation, quotations, relevant media description and observation time.
A pasted excerpt with missing context is a hold case, not complete evidence.
Use real examples you are authorized to retain. Generated examples are useful
for engineering tests but cannot establish production accuracy.

Build at least 200 held-out cases, with at least 40 English and 40 Arabic cases.
Include Gulf Arabic and mixed-language writing. Each release category needs
at least five cases: `gulf_arabic`, `mixed_scripts`, `sarcasm`, `negation`,
`quotation`, `multiple_entities`, `injection`, `source_contradiction`,
`checker_outage`. Categories may overlap. Include ordinary posts, wrong or
incidental targets, competing subject cards, unknown facts, outdated sources,
untrusted summons and examples that should receive no reply. Do not select
only easy posts or successful drafts.

Keep an annotation sheet with these columns:

| Field | What a reviewer records |
|---|---|
| Case ID and language | Stable unique ID; `en`, `ar` or `mixed` |
| Original context | Exact text, authorship, quote/media context and timestamp |
| Categories | Applicable failure categories from the list above |
| Expected target | The actual subject, or an empty string when none is justified |
| Expected auto | Whether this case should be eligible under the declared rules |
| Expected evidence/safety | Whether the candidate is supported and safe |
| Rationale and sources | Why the labels are correct; independently checked source passages |
| Labeler and review status | Named human reviewer; disagreements resolved before release |
| Held-out status | True only if the example was excluded from tuning |

Label target and eligibility before seeing the model decision when possible.
Have a second reviewer independently inspect difficult Arabic, sarcasm,
quotation and safety cases. Resolve disagreements explicitly; leave unresolved
cases out of release qualification and retain them for investigation. Do not
mark a case human-reviewed merely because a model supplied labels.

## Record actual outcomes

Run the frozen model/policy against the held-out cases in an isolated,
read-only evaluation workflow. Never call a publishing endpoint to evaluate a
case. Studio Preview helps inspect drafts and typed checks but intentionally
skips summoner authority and publication quotas: it is **not a complete
unattended eligibility evaluator**. A passing preview alone is insufficient
for a release report. Full shadow evaluation must exercise the same authority,
context, routing, evidence, caps and five independent checks as the real path,
with external writes replaced by a recording transport in an isolated test
installation. A production report needs actual recorded decisions from that
workflow, not invented successes or predictions inferred from a fluent draft.

Collect stored observations with the CLI from the installation whose policy
you intend to evaluate:

```powershell
python scripts/x_shadow.py --collect collected.json --limit 1000
```

This exports tenant-scoped original context and observed candidates with empty
labels. Archived records are included, so difficult or denied cases are not
silently excluded. Fill the annotations, split by conversation, and save a
separate held-out collection. Missing recorded context remains explicitly
unverified. The collector never converts an old excerpt into verified context.

Keep the master mode **draft**. Before freezing the release policy, grant
`allow_auto` only to the small subject allowlist you plan to qualify; draft mode
continues to require review. Changing these permissions after evaluation would
invalidate its fingerprint. Then run:

```powershell
python scripts/x_shadow.py --evaluate held-out.json --output observed.json
```

The runner captures settings, knowledge and publication history in an isolated
snapshot. Each case executes the real authority, drafting, five-check and
publication-preflight path in a fresh child process. It simulates activation
without changing the live settings, and records send intent after preflight;
all X transport and alert delivery are blocked. Model endpoints may be called
and charged normally. Local-only remains enforced. Keep indexing quiet during
capture; a changing vector index or pipeline invalidates the snapshot.

Each case starts from the same captured quota/history baseline. This measures
independent decision eligibility, rather than a campaign's aggregate capacity.
Existing published or uncertain cases retain their idempotency holds. Use new
held-out cases for measuring future eligibility, and retain already handled
cases as recovery tests. For checker-outage coverage, set a real case's `fault`
to `checker_outage`; this deliberately makes verification unavailable.

The runner preserves expected labels but always clears `human_reviewed` and
leaves actual critical violations unset. A reviewer must inspect the actual
candidate and complete those fields before qualification. Missing provider
usage is reported, not estimated as zero; an incomplete measurement cannot
qualify. Import the shadow report into a release collection in **Datasets**.
Inspect each recorded candidate, complete the human labels and enter the actual
critical-violation count, then attest your review. Model outcomes are read-only
in the editor. Changing source or summon context clears its old outcome because
it no longer describes that case. **Export Reviewed Outcomes** produces the
report for the separate administrator qualification upload. A JSON editor or
annotation sheet also works; no model generates the human labels. Preserve the
runner's measured outcomes when adding labels.

Record the pipeline fingerprint, exact model identities, candidate, all five
check verdicts, auto-eligibility decision, target, measured latency, model calls
and output tokens. A human also checks the actual candidate for critical
violations. Checker outages, ambiguous context and unsupported assertions must
produce holds. Do not substitute a later model output for the one reviewed.

## Validate and install

The release report format is described in [X auto-reply](x-auto-reply.md).
Use Studio's qualification panel or run:

```powershell
python scripts/x_qualification.py
python scripts/x_qualification.py --report reviewed.json
```

The first command prints the current fingerprint and requirements. The second
computes metrics and rejects incomplete or stale reports without installing.
At least 50 actual predictions must be auto-eligible, eligibility precision
must be at least 98%, and there must be zero critical violations, wrong-target,
unsupported-fact or unchecked auto outcomes. An all-held run cannot certify
unattended publishing. Review hold rate, false holds, latency and usage too;
a high precision score does not mean every useful case is answered.

Only an administrator installs an accepted report, using Studio upload or:

```powershell
python scripts/x_qualification.py --report reviewed.json --install
```

Installation does not enable auto mode. The owner must separately choose auto mode and grant
it to specific subject cards. Mode activation alone preserves the identical
evaluated fingerprint; changes to policy, models, account, evidence configuration
or generation/validation code require a fresh evaluation. Reports expire after
14 days. Keep the original cases, labels, outcomes and report together so you
can investigate mistakes and reproduce the evaluation.

## Grow it gradually

Collect held drafts and failed routing cases during normal attended use.
Record edits and denials as learning signals; an edit is not automatically a
correct human label. Add the failures to the tuning set, freeze improvements,
and evaluate against a separate untouched release set. Replace leaked or tuned
held-out cases with newly collected independent ones. Keep automatic replies
held until the full release criteria are met.
