---
title: Building an X evaluation dataset
sidebar_position: 18
---

Start with **draft mode**. You can use X Studio with an exact local model,
manual post drafting and attended review before you have a dataset. Keep
`allow_auto` disabled on subject cards while you tune them. Missing evaluation
never grants unattended publication.

## Collect real examples

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

The current release includes report validation and upload. A turnkey corpus
annotation application and real-network shadow runner are not included. Until
that workflow and real cases are available, continue draft mode; do not fill
`actual` outcomes by guessing.

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
