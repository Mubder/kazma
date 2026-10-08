# HA qualification status and next bounded trial

The next candidate must qualify recovery with its new IDE and swarm receipts.
Local and PostgreSQL tests cannot substitute for the intended cluster's node
and storage behavior. No new paid resources have been provisioned for this plan.

## Existing cloud evidence

Two disposable trials ran in `ivory-pathway-500221-f2` on 2026-10-06. The
follow-up used source `bcccd05927a6eea0909e9357c8a8d951e75d4d1b`, two private
GKE workers in separate zones, regional PostgreSQL 16 and a regional
ReadWriteOncePod volume. Both trial inventories independently confirmed removal
of their owned resources. The original failures and narrower image scopes are
preserved in the operator's ignored report receipts; they are not rewritten as
current-revision results.

| Observation on follow-up source | Recorded result |
| --- | --- |
| Database and volume contenders | Refused while the original owner was active |
| Paired quiescent restore | Workspace/vector bytes, vault decryption, approvals and effect receipts matched |
| Restored pending approval | Actual Deny settled; resumed transcript reported denial; denied-write marker absent |
| SQL-connectivity partition | App exited 75 while the original VM was still running; restart could not acquire ownership |
| Physical fence before replacement | Provider confirmed TERMINATED before successor creation; disk had one successor in the other zone |
| Receipt crash window | Completed result reused; interrupted write held; fixture markers remained once |

The follow-up measured 934.437 seconds to restored readiness and 964.781 seconds
to complete decision/transcript verification; cross-zone recovery and state
verification took 284.438 seconds. These are observations, not availability or
zero-data-loss guarantees. The fault retained Kubernetes connectivity; complete
control-plane partition, region loss and long soak were not tested. Database
primary failover passed on the earlier trial image, not on the follow-up image.
The source subsequently merged through [PR 100](https://github.com/Mubder/kazma/pull/100).

## Candidate trial acceptance

Pin one exact CI-qualified candidate and immutable image digest for every
scenario. Preserve one active owner per paired database/complete state volume,
use independent failure-domain nodes, and keep integrations synthetic. The live
Windows installation and unrelated cloud resources are excluded.

Before provision, verify account/project access, current regional prices and
quotas, the CSI class's documented node fencing, and a fresh operating allowance.
Both earlier trial windows are closed; their authorization files cannot admit
another paid create. Define fixed test-stop, teardown and final-cleanup deadlines
with one cleanup owner. Do not extend the clock after admission.

| Scenario | Required evidence |
| --- | --- |
| Current-image normal pod restart | Same persisted state and pending gate; actual Deny with no effect |
| IDE response loss | Same operation key returns the saved result; one invocation and one approval |
| Swarm mutator failure | Uncertain effect held; no synthesis, retry or fallback invocation |
| Ownership session and database primary failover | Old process stops; it does not reconnect/reacquire ownership silently |
| SQL-only partition | Fail-stop while the VM remains alive; no replacement until physical/storage fence confirmed |
| Node/control-plane isolation | Preserve logs of the old process and provider-confirmed physical fence before replacement; never force-delete an unfenced StatefulSet identity |
| Paired restore | Original vault key, checkpoints, approvals and receipts agree; completed effects reused and unknown effects held |
| Cleanup | Independent absence inventory for every owned paid, network and identity resource; unrelated resources untouched |

Record timestamps, node/pod events, volume attachments, lease/session IDs,
readiness, receipt metadata, approval decisions and disposable target markers.
For every effect compare the external marker count before and after recovery;
readiness alone cannot establish safe execution. Declare RTO/RPO targets before
the run and report measured results, failed drills and unrun scenarios separately.

The operational steps and fencing restrictions remain in
[Multi-region and HA](../docs/ops/multi-region.md). Local receipts do not make
remote APIs atomic. Automatic whole-agent Temporal retries remain disabled;
custom workers that bypass shared executors need their own effect contract.
