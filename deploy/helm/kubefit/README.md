# KubeFit Helm chart

This chart deploys the existing stateless KubeFit HTTP API. It does not install
Prometheus, run the operator-side analysis CLI, mutate workloads, or publish GitHub
pull requests from inside the cluster.

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
