# Architecture

KubeFit uses a ports-and-adapters boundary around a deterministic recommendation
domain. Collection and Git hosting are replaceable infrastructure concerns.

```text
Kubernetes API ----\
                    collector -> recommender -> evaluator -> GitOps patch/PR
Prometheus API ----/                    |
                                         +-> FastAPI -> dashboard
```

The recommendation path is read-only. Writing occurs only in a configured Git
repository, and the initial pull request is a draft. Cluster rollout remains the
responsibility of the repository's existing GitOps controller and human approval
policy.

## Historical workload identity

The collector reads the current Deployment UID and retains only live ReplicaSets
whose controller owner UID matches it. Container usage is then joined with
`kube_pod_owner` for that exact ReplicaSet allowlist at each Prometheus range-query
timestamp, preserving a separate series per Pod.

The query begins no earlier than the current Deployment creation time, while
coverage remains relative to the full requested window. This prevents a same-name
recreation from inheriting older evidence and keeps new workloads non-actionable.
kube-state-metrics ownership retention remains an explicit prerequisite. ReplicaSets
already removed from the Kubernetes API require the optional identity snapshot to
have observed them before deletion.

An optional local identity snapshot provides that mapping for ReplicaSets KubeFit
has already observed. Records merge only while the Deployment UID remains identical;
a new UID replaces same-name history. Writes are atomic, and malformed or unsupported
snapshot schemas fail closed. This single-process adapter is not a shared audit store
or a replacement for a future controller-backed identity source.

The disposable local environment gives Prometheus a 5 GiB `standard` PVC, keeps a
two-day time horizon, and caps TSDB retention at 4 GB to leave WAL and compaction
headroom. This preserves observation evidence across Prometheus Pod and Docker
restarts. The volume belongs to the kind cluster and is not an external evidence
archive; deleting the cluster deletes the claim and restarts coverage accumulation.

## Recommendation policy v0

- CPU request: observed P95 plus 25% margin, rounded up to 10 millicores
- Memory request: observed P99 plus 25% margin, rounded up to 16 MiB
- CPU limit: 2x recommended request
- Memory limit: 1.5x recommended request
- Enforce small non-zero floors for idle or incomplete observations
- Calculate each percentile per Pod and retain the busiest Pod's value
- Require 70% observation coverage and at least 100 metric samples
- Require desired, available, and observed replica counts to match
- Require CPU throttling coverage and samples to meet the same thresholds
- Require target-container status for every desired replica
- Treat CPU throttled-period P95 of at least 1% as medium risk and 10% as high
- Treat any current OOMKilled container status as high risk

These defaults are deliberately transparent and deterministic. Restarts are exposed
as evidence but are not automatically attributed to memory pressure. Application
latency and traffic representativeness still need to be added before production use.

CPU and memory request changes are reported separately because millicores and MiB
cannot be combined into a meaningful percentage. The evaluator converts both into
USD only after the caller provides CPU and memory hourly prices, a price source,
replica count, and monthly hours. It returns CPU and memory components separately
and identifies the calculation basis as `resource_requests`.

```text
CPU cost    = request mCPU / 1000 × core-hour price × hours × replicas
Memory cost = request MiB / 1024 × GiB-hour price × hours × replicas
```

The evaluator uses exact decimal arithmetic. Prices and calculated money are
serialized as decimal strings so JSON transport does not silently reintroduce
binary floating-point rounding.

Resource calculation and change authorization are separate. An insufficient result
still includes its candidate and evidence for inspection, but future patch generation
must accept only an evaluation whose `patch_eligibility.status` is `eligible`.

## Observation readiness projection

The read-only readiness command reuses the exact workload, Prometheus, identity,
recommendation, and eligibility path used by analysis. For the requested window it
converts the 70% coverage threshold into a required sample count and takes the
larger of that value and the policy's 100-sample floor.

```text
required = max(100, ceil(points-per-Pod × desired replicas × 70%))
remaining intervals = ceil(max(usage deficit, throttling deficit) / replicas)
```

An estimate is emitted only when replicas, usage/throttling Pod coverage, and
container statuses are complete and no high-risk signal is already present.
Otherwise the status is `blocked`, because passage of time alone is not a defensible
remediation. Estimates remain projections: a rollout, scrape gap, OOM, throttling
change, or replica change invalidates their assumptions.

## Patch eligibility policy v0

```text
recommendation readiness ─┐
OOM risk                  ├─> structured checks ─> eligible | blocked
CPU throttling risk       ┘
```

- Insufficient readiness blocks a proposal.
- High or unknown OOM and throttling risk blocks a proposal.
- Medium risk remains eligible for a draft proposal but emits a reviewer warning.
- Projected savings and upsize/downsize direction do not grant or remove eligibility.

This gate authorizes only manifest proposal generation. It does not authorize a
merge, cluster mutation, or rollout.

## Manifest proposal boundary

The manifest generator consumes the complete evaluation so the observed current
resources, recommendation, eligibility, warnings, and evidence cannot be supplied
as unrelated arguments.

```text
eligible evaluation + YAML sources + exact target
  -> unique Deployment/container match
  -> stale resource comparison
  -> four scalar-span replacements
  -> patched content + unified diff + SHA-256 report
```

PyYAML is used to compose a syntax tree and obtain scalar source positions. KubeFit
does not serialize the tree back to YAML; it replaces only selected scalar spans in
the original text. This preserves unrelated formatting and comments byte-for-byte.
Blocked evaluations, duplicate targets or fields, aliases, missing resource maps,
invalid quantities, and repository values that differ from the evaluation all fail
before an artifact is returned.

The generator is pure: it does not write the repository or touch the cluster.

## Analysis identity boundary

CLI analysis output binds the evaluation to namespace, Deployment, container,
Deployment UID, and creation timestamp. Proposal creation derives its target from
this artifact rather than accepting retyped identity, and includes `analysis.json`
in the content digest. Before benchmark mutation, the live Deployment UID and
creation timestamp must still match. This extends stale resource checks across
same-name workload recreation.

Manifest sources are loaded only from an explicit repository root. Traversal,
outside files, duplicates, directories, invalid UTF-8, and symlinked roots or path
components fail before patch generation. Stored source paths are stable POSIX paths
relative to that root.

## Immutable proposal artifacts

The artifact writer turns a pure patch into stable input for benchmark and Git
workflows without changing the source repository.

```text
evaluation + original/candidate manifest + diff/report
  -> canonical payload bytes
  -> content digest and per-file SHA-256 index
  -> private staging directory
  -> fsync
  -> atomic directory rename
  -> immutable proposal-<digest>
```

The bundle contains no generated timestamp, so identical inputs produce the same
ID across output locations. A cooperative exclusive publication lock serializes
writers. If the destination already exists, every file and byte must match before
the existing bundle is reused; extra files, symlinks, and changed bytes fail closed.

Benchmark output does not belong inside this bundle. A benchmark run must create a
separate result artifact that references the proposal ID, preserving the proposal as
an immutable before/after input.

Full `manifests/before/<path>` and `manifests/after/<path>` payloads preserve the
repository change for review. They are not executable benchmark inputs. The writer
also slices the patch report's selected YAML document, reparses it, and verifies its
`apps/v1 Deployment` target and container before storing
`benchmark/manifests/before.yaml` and `after.yaml`. The loader derives those files
again from the full sources and requires byte equality after all hashes pass. This
prevents a multi-document source from expanding one benchmark into reconciliation
of neighboring Kubernetes objects.

## Benchmark comparison boundary

The checked-in `kubefit-load-v1` k6 profile fixes warmup, steady, spike, and recovery
arrival rates and timing. Its compact result records expected and completed
iterations separately from HTTP request count, along with per-phase errors and tail
latency. Before/after measurements add Prometheus throttling, Kubernetes OOM and
restart evidence, recovery time, and request cost.

The verdict first rejects results that do not reference the same proposal and fixed
offered load. Only comparable runs reach the safety policy. Safety failures and cost
change remain independent outputs, preventing projected savings from masking a
latency, error, throttling, recovery, or OOM regression. The current module defines
this policy as a pure contract, independently of cluster mutation and artifact I/O.

## Restoring benchmark execution

The execution core loads and rehashes every proposal payload before invoking a
cluster controller. It then applies and measures before and after sequentially. As
soon as the first apply begins, every exit path attempts to reapply before and wait
for its Deployment rollout. A successful result is returned only after restoration;
if execution and restoration both fail, both causes remain available to the caller.

The kubectl adapter requires an explicit context and bounded rollout timeout. It
receives only the isolated single-Deployment manifests; full multi-document source
payloads remain reviewer evidence and are never passed to `kubectl apply`. The
measurement collector remains injected and composes k6 with aligned
Prometheus/Kubernetes evidence. This mutation workflow is still restricted to a
disposable benchmark cluster because each apply reconciles the complete selected
Deployment, not only its four resource scalars.

## Aligned measurement evidence

One measurement brackets k6 execution with Pod-level runtime snapshots, then queries
CPU throttling only from the recorded run interval. Stable Pod identity is required;
replacement or decreasing counters invalidates collection. A custom raw k6 marker
anchors five-second recovery windows, while the proposal's validated evaluation
supplies the matching current or recommended monthly request cost.

The typed provenance stores the run boundaries, Pod set, Prometheus rate window,
and hashes of the k6 summary and raw stream. Candidate OOM is an absolute failure,
incomplete candidate recovery is a failure, and incomplete baseline recovery makes
the comparison invalid. The execution result retains raw stream bytes for the
immutable result publisher.

## Immutable benchmark results

The restoring run carries exact k6 summary/raw bytes until publication. Before any
write, the publisher checks those bytes against measurement provenance, reparses the
summary, verifies proposal/variant identity, and recomputes the verdict. It then
publishes canonical before/after measurements, exact raw evidence, verdict, and a
generated Markdown report under a content-derived `benchmark-<digest>` ID.

Result publication uses the same private staging, `fsync`, exclusive lock, atomic
rename, and byte-exact retry principles as proposal publication, but writes to a
separate root and never modifies proposal inputs. Restricted k6 system tags and URL
validation keep common URL credentials out of retained evidence. The publication
lock does not serialize cluster mutation; the local CLI wraps the wider workflow in
the separate Deployment-scoped execution lock below.

## Deployment-scoped execution lock

The local benchmark command hashes explicit kubectl context, namespace, and
Deployment into a lock filename and acquires a non-blocking POSIX advisory lock.
The kernel releases ownership on process exit, avoiding stale PID-file cleanup.
Symlinked roots and files are rejected and the lock is held across proposal
revalidation, both applies and measurements, restoration, and result publication.

The CLI accepts only an explicit `kind-*` context plus a required disposable-cluster
acknowledgement. This is a product boundary rather than a claim that lower-level
adapters are production safe. Locks coordinate only local cooperating processes;
distributed execution remains outside the MVP.

## Verified pull request plan

The GitHub boundary begins with a pure plan rather than a network request. Proposal
loading regenerates the minimal patch from the full before source and evaluation,
then requires the persisted after source, diff, report, and isolated benchmark files
to agree. Result loading verifies its canonical index and exact file set, reparses
measurements and raw k6 evidence, recomputes the verdict, and regenerates its
Markdown report.

Only a `pass` result referencing the exact proposal and the proposal-fixed before
and after costs can produce a plan. The plan contains one repository-relative path,
its expected before SHA-256, exact before/after bytes, deterministic branch/title,
draft-only flag, evidence summary, and rollback guidance. A later repository adapter
must compare the live file hash again before writing; planning itself performs no
checkout, commit, push, or GitHub operation.

## Transactional repository commit

The local repository adapter requires the explicit root to equal Git's top-level,
an attached branch, and a completely clean tracked/untracked status. It rejects
symlinked roots and planned path components, then compares both SHA-256 and exact
source bytes before creating the deterministic branch.

The adapter atomically replaces only the planned file, stages only that path, and
commits without changing Git configuration. It verifies the generated commit has
the original base as its sole parent, exactly one changed path, unchanged Git file
mode, exact planned blob bytes, and the planned subject. It then returns to the base
branch and requires a clean tree. A matching existing one-commit branch is reused;
all other collisions fail without overwrite.

If a failure occurs after branch creation, cleanup restores and unstages only the
adapter-owned file, switches back, and deletes only the newly created branch. It
does not reset or erase unrelated paths. Repository hooks are honored; their
external side effects cannot be rolled back, and unexpected hook-created files are
reported as cleanup failure instead of being deleted.
