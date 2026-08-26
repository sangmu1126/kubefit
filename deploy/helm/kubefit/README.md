# KubeFit Helm chart

This chart deploys the stateless KubeFit HTTP API and its immutable review
dashboard in one image. It does not install Prometheus, run the operator-side
analysis CLI, mutate workloads, or publish GitHub pull requests from inside the
cluster.

## Published installation

After the `Release packages` workflow has passed its anonymous image and chart pull
gate for a version, install the OCI chart without a source checkout:

```bash
helm upgrade --install kubefit \
  oci://ghcr.io/sangmu1126/charts/kubefit \
  --version 0.3.1 \
  --namespace kubefit-system \
  --create-namespace \
  --wait
```

The chart defaults to `ghcr.io/sangmu1126/kubefit:<appVersion>`. Do not treat the
command as available merely because the chart renders: the release workflow must be
green, including its final job with no registry login. On the first GHCR publication,
the repository owner may need to make both `kubefit` and `charts/kubefit` packages
public and rerun the workflow.

## Local kind installation

```bash
docker build -t kubefit:dev .
kind load docker-image --name kubefit kubefit:dev

helm upgrade --install kubefit deploy/helm/kubefit \
  --namespace kubefit-system \
  --create-namespace \
  --set image.repository=kubefit \
  --set image.tag=dev \
  --set image.pullPolicy=Never \
  --wait
```

Verify the health endpoint without exposing a public service:

```bash
kubectl --context kind-kubefit \
  --namespace kubefit-system \
  port-forward service/kubefit 8000:80

curl --fail http://localhost:8000/healthz
curl --fail http://localhost:8000/
```

The image runs as UID/GID `10001`. The Pod defaults to a read-only root filesystem,
dropped capabilities, RuntimeDefault seccomp, explicit requests/limits, and no
mounted service account token.

## Optional observation RBAC

The current API does not use Kubernetes credentials. Leave the default target list
empty unless an in-cluster observation entry point is deliberately added and
reviewed. For that future boundary, access must be explicitly scoped by namespace:

```bash
helm upgrade --install kubefit deploy/helm/kubefit \
  --namespace kubefit-system \
  --create-namespace \
  --set serviceAccount.automountToken=true \
  --set 'rbac.targetNamespaces[0]=kubefit-demo'
```

For each target, the chart creates a `Role` and `RoleBinding`, never a ClusterRole.

| API group | Resource | Verb |
|---|---|---|
| `apps` | `deployments` | `get` |
| `apps` | `replicasets` | `list` |
| core | `pods` | `list` |

The chart rejects target namespaces unless RBAC creation and token automount are both
explicitly enabled. Reusing an existing service account also requires a non-empty
name, preventing accidental grants to the namespace's shared `default` account.

## Validation

```bash
helm lint deploy/helm/kubefit
helm template test deploy/helm/kubefit --namespace kubefit-system
pytest -q tests/test_helm_chart.py
```

The tests inspect rendered resources and fail on cluster-scoped RBAC, unexpected
verbs/resources, missing security context, invalid namespace values, and incomplete
RBAC identity settings.

For an end-to-end check against the existing disposable `kind-kubefit` cluster:

```bash
./deploy/local/verify-kubefit-chart.sh
```

The script builds and loads `kubefit:dev`, forces a disposable-cluster rollout for
that mutable local tag, installs the tokenless release, probes `/healthz` and the
packaged dashboard, temporarily enables one namespace Role, verifies both allowed
and denied actions with ServiceAccount impersonation, and restores the
tokenless/no-RBAC release. An EXIT trap attempts restoration if the scoped-RBAC
phase is interrupted.

## Local image inventory

After building the image, generate a verified SPDX inventory with Docker Scout:

```bash
./deploy/local/generate-image-sbom.sh
```

The generated artifact is bound to the full local image ID and stored under the
ignored `.kubefit/supply-chain/` directory. It verifies hashes and package
assertions on reuse, but it is not a vulnerability scan, signature, or published
release attestation.
