# X Studio remaining acceptance and operator steps

Updated: 2026-10-05. Engineering status: [execution record](X_STUDIO_EXECUTION.md).

## Current evidence

- Text posting succeeded, and the published receipt survived reload.
- A genuine trusted summon generated a Support reply. Telegram approval
  published it; this was attended approval, not an automatic-reply canary.
- The deployed model compatibility sample passes target, stance, evidence
  and safety after verifier prompt corrections. Pasted sample context stays
  unverified. A passing sample is not human accuracy qualification.
- The starter dataset has 15 real observations, 12 conversation groups and
  zero human reviews. The current test conversation has been used for tuning;
  do not place it in the held-out release dataset.
- One actual PostgreSQL archive restored with data into a scratch database
  in 21 seconds. Whole-install recovery and production recovery objectives
  remain unmeasured.

## First operator session: five cases

1. Open X Studio → Datasets → Starter — Real X Observations. Choose five
   real cases spanning English, Arabic, a quotation or ambiguous target, and
   a case that should receive no automatic reply.
2. Open the original post and its conversation. Check exact text, author,
   source ID, quoted material and relevant media. Do not mark incomplete
   context verified; label the expected hold instead.
3. In the case editor, set language and applicable categories. Set expected
   target to the actual subject card, or label no target when none applies.
   State whether unattended eligibility, factual support and safety should
   pass under the intended saved policy. Unknown labels may stay empty.
4. Write a short rationale: for example, “This quotes another person;
   criticism must address the quoted claim, not invent the author's belief.”
   Resolve disagreements before attesting review.
5. Save. Human attestation must be supplied by the person who actually
   reviewed the case. The agent can help explain fields and organize data,
   but cannot manufacture the human review.

Begin with five to validate the workflow; five do not qualify automatic mode.
The current starter observations are for investigation and tuning. Retain
already published or uncertain cases as recovery examples; they cannot measure
future automatic eligibility because the normal idempotency rules hold them.

## Frozen release evaluation

Create separate tuning and held-out release collections. Keep every related
conversation in one set. Do not tune on the held-out cases. Collect at least
200 real held-out cases with at least 40 English and 40 Arabic cases and five
cases in every required category:

`gulf_arabic`, `mixed_scripts`, `sarcasm`, `negation`, `quotation`,
`multiple_entities`, `injection`, `source_contradiction`, `checker_outage`.

The operator confirms the intended subject scopes, permitted tones/language,
model bindings and small eventual automatic subject allowlist. Engineering can
then freeze the configuration, run the isolated shadow evaluator and import
its recorded outcomes. The human reviewer inspects those actual candidates,
completes labels and critical-violation counts, and attests review. No X posts
are needed for shadow evaluation. Model endpoint calls can incur normal usage.

The report requires at least 50 actual auto-eligible predictions, at least 98%
eligibility precision, and zero critical, wrong-target, unsupported-fact or
unchecked automatic outcomes. A run that holds everything cannot qualify.
Installation and activation are separate administrator actions. Relevant code,
policy/model/account changes invalidate evaluation; reports expire after 14 days.

See the [full collection guide](../docs/guide/x-evaluation-dataset.md).

## Remaining ownership

| Work | Agent can complete | Operator contribution |
|---|---|---|
| Final release CI and deployment | Diagnose failures, fix code, verify deployed commit and health. | None unless the external service requires sign-in. |
| Evaluation | Prepare collections, validate provenance, run isolated shadow evaluation, summarize failures and install an accepted report when authorized. | Label authentic examples, decide intended behavior, review actual candidates and attest judgment. |
| Local X model | Configure the independent binding and test it when a suitable endpoint is available. | Start or identify the desired local server/model; choose the desired model. No secret needs to be sent in chat. |
| Operational recovery | Build isolated whole-bundle restore and rollback rehearsals, exercise unknown-send/notification behavior and record measurements. | State acceptable downtime and data loss; attend any exercise that could interrupt actual service. |
| Accessibility/operator acceptance | Run automated EN/AR keyboard/mobile checks and fix findings. | Try actual review/edit/deny flows with any assistive technology you use and report problems. |
| Media and alt text | Implement account capability detection, authentication integration, immutable attachment/alt-text review, durable upload/publication recovery, UI and tests. | Complete the account authorization screen if new media permissions are needed. |
| Campaigns | Implement reviewed campaign manifests, scheduling, quotas, cancellation and partial recovery through the existing publication service. | Confirm campaign purpose, audience and planned limits before live operation. |
| Analytics | Implement supported API metrics, provenance, missing/denied-data states and bounded retrieval. | Provide account access through the product authorization flow where required. |
| Test cleanup | Delete the two known test posts and verify receipts after action-time confirmation. | Confirm permanent deletion; X cannot undo it. |

Media, alt text, richer campaigns and external analytics remain code jobs;
they have not been delivered. Account permissions do not implement them.
The current media upload documentation specifies OAuth 2.0 `media.write`:
[official upload reference](https://docs.x.com/x-api/media/upload-media).
The live four-key OAuth 1.0a text connector does not by itself establish this
capability. Build and review the new flow before asking the operator to grant it.

## Next sequence

Finish release verification and test cleanup, review five starter cases, finish
isolated deployment acceptance, then freeze the release policy and build genuine
held-out coverage. Richer features can be engineered behind disabled boundaries;
live acceptance follows their verified account capabilities and applicable text
readiness gates. Keep ordinary automatic publishing held during qualification.
