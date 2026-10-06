---
id: multi-region
title: Multi-Region & HA
sidebar_label: Multi-Region & HA
description: Prepare and qualify Kazma's single-owner active/passive deployment
---

# Multi-region and HA qualification

Kazma's HA preparation profile runs **one active runtime owner**. A replacement
may start after the previous owner has been fenced and the complete state volume
has moved safely. Postgres support in individual stores does not make every
runtime store shared or permit horizontal active replicas.

The shipped Kubernetes manifest is a preparation template. Local ownership
tests and disposable Postgres drills have passed; actual cluster storage
fencing, node failure and cross-region recovery still require qualification.

## Recommended staging architecture

Use a managed Kubernetes cluster with Linux workers in independent failure
domains, managed HA Postgres with backups and point-in-time recovery, and a
replicated block-storage CSI driver whose detach and fencing behavior is
documented. Keep staging separate from the live installation and give it its
own database, secrets, storage and test-only integrations.

| Component | Contract |
|-----------|----------|
| Kazma | One StatefulSet replica, pinned release image digest |
| Database | Dedicated Postgres ownership session; direct endpoint or session pooling, never transaction pooling |
| State | Complete `/state` volume: data, home, skills, vector memory and configured document/workspace storage |
| Storage | `ReadWriteOncePod`, CSI support and demonstrated old-node fencing |
| Routing | TLS ingress; readiness at `/health/ready`; correct trusted proxy addresses |
| Recovery | Paired database/state backup, original vault key, measured restore drill |

`ReadWriteOnce` can allow more than one pod on the same node. Use
`ReadWriteOncePod` with a compatible CSI driver; the access mode alone does
not prove safe recovery from an unreachable node. See the
[Kubernetes storage access-mode documentation](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#access-modes).

## Find an existing staging cluster

These PowerShell commands discover access without creating cloud resources:

```powershell
kubectl config get-contexts
gcloud auth login
gcloud config get-value project
gcloud container clusters list
```

The Google Cloud commands apply when that account is the chosen provider.
Review the project and cluster purpose before choosing a target. If a staging
cluster exists, obtain its context using its actual name, location and project:

```powershell
gcloud container clusters get-credentials CLUSTER_NAME --location LOCATION --project PROJECT_ID
kubectl config current-context
kubectl get nodes -o wide
kubectl get storageclasses
kubectl get csidrivers
```

See [Google's cluster-access guide](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl)
for the required authentication plugin and credential command.

If sign-in cannot refresh automatically, the owner must complete the interactive
login. If no suitable cluster exists, provisioning requires a chosen project,
region, spending limit and the storage/backup plan above. A local Docker or kind
cluster can exercise pod recovery but cannot qualify cross-host HA.

## Prepare the deployment

1. Create a dedicated staging namespace and a separate managed Postgres database.
  Record the Kubernetes context explicitly for every deployment command.
2. Select a CSI storage class with replicated storage and confirmed
  `ReadWriteOncePod` support. Read the provider's node-loss fencing procedure.
3. Copy `deploy/kubernetes/runtime.yaml` into an operator-managed deployment
  configuration. Replace the image placeholder with a qualified digest and the
  storage-class placeholder with the chosen class. Keep `replicas: 1`.
4. Populate the referenced `kazma-runtime-secrets` secret through the approved
  secret manager: `database-url`, `auth-secret`, `vault-key`, `trusted-proxies`.
  Set provider credentials through the same protected workflow. Do not commit
  secret values. The database URL must preserve the ownership session.
5. Verify every configured persistent path resolves under the complete state
  volume. Migrate stores once using the migration runbook before starting the
  server; the template disables automatic migration.
6. Review the rendered manifest, deploy only to staging, and verify readiness,
  authentication, a harmless chat, approval/deny behavior and state persistence.
7. Configure coordinated Postgres and state-volume backups. Restore them into
  a fresh staging environment before beginning failover drills.

The runtime holds a local OS writer fence. `KAZMA_RUNTIME_HA=1` adds a
dedicated Postgres ownership session and binds that database to the complete
state volume. Loss of the ownership session stops the process rather than
reacquiring ownership while effects could still be uncertain. Storage fencing
must also prevent an old host writing after a replacement starts.

## Run the qualification drills

Use synthetic work and disposable targets. Save timestamps, readiness responses,
owner/session evidence, pod/node events, volume attachment events, approval rows,
tool receipts and the external target's audit log for each drill.

| Drill | Required result |
|-------|-----------------|
| Pod exits during a read | Replacement becomes ready and persisted chat remains readable |
| Pod exits after a mutating invocation | Unknown effect is held; no duplicate effect or fabricated success |
| Restart with an unanswered approval | Same pending gate survives; deny prevents execution |
| Restart during an approval resume | Human decision survives; execution is unconfirmed and old buttons stay closed |
| Second runtime attempts ownership | It is refused while the first owner is alive |
| Postgres ownership session is lost | Old process stops; it never silently reacquires ownership |
| Worker node fails or is partitioned | Old writer is demonstrably fenced before volume attaches and replacement becomes ready |
| Database failover | Ownership loss/recovery follows the same fencing contract; no overlapping owners |
| Restore paired backups | Vault, graph checkpoints, approval decisions and tool receipts agree; target evidence is reconciled before new writes |

Start with normal pod termination. Have the cluster operator perform node and
network-failure drills using the provider's procedure. Do not force-delete an
unreachable StatefulSet pod to hurry recovery before proving its old process is
dead or fenced: a replacement can otherwise duplicate its identity. See
[Kubernetes StatefulSet force-deletion guidance](https://kubernetes.io/docs/tasks/run-application/force-delete-stateful-set-pod/).

Declare acceptable RTO and RPO before the drill. Measure outage to restored
readiness, then verify correct work can resume; a green health endpoint alone
does not prove effect safety. Qualification requires no concurrent writers,
no duplicated external effects and successful paired restores. Keep failure
evidence and fix the cause before repeating the affected drill.

## Region recovery

Begin with one active region. A secondary region needs recoverable paired
database/state backups and the same vault key, with its runtime stopped until
the original owner and storage are fenced. Test restoration and routing cutover
before claiming a regional RTO. Active-active cross-region execution remains
unqualified.

See [Disaster recovery](./disaster-recovery),
[Postgres & SaaS](./postgres-and-saas) and the deployment guide.
