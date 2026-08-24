import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy/helm/kubefit"


def helm(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    if shutil.which("helm") is None:
        pytest.skip("Helm is not installed")
    return subprocess.run(
        ["helm", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def render(*args: str) -> list[dict[str, object]]:
    result = helm("template", "test", str(CHART), "--namespace", "kubefit-system", *args)
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def by_kind(documents: list[dict[str, object]], kind: str) -> list[dict[str, object]]:
    return [document for document in documents if document["kind"] == kind]


def test_chart_lints() -> None:
    result = helm("lint", str(CHART))

    assert "0 chart(s) failed" in result.stdout


def test_default_chart_is_non_root_tokenless_and_has_no_rbac() -> None:
    documents = render()

    assert {document["kind"] for document in documents} == {
        "Deployment",
        "Service",
        "ServiceAccount",
    }
    service_account = by_kind(documents, "ServiceAccount")[0]
    assert service_account["automountServiceAccountToken"] is False
    deployment = by_kind(documents, "Deployment")[0]
    pod_spec = deployment["spec"]["template"]["spec"]
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["securityContext"] == {
        "fsGroup": 10001,
        "runAsGroup": 10001,
        "runAsNonRoot": True,
        "runAsUser": 10001,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    container = pod_spec["containers"][0]
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
    }
    assert container["resources"]["requests"] == {"cpu": "100m", "memory": "128Mi"}
    assert container["resources"]["limits"] == {"cpu": "500m", "memory": "512Mi"}
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert container["readinessProbe"]["httpGet"]["path"] == "/healthz"
    assert pod_spec["volumes"] == [{"name": "tmp", "emptyDir": {}}]


def test_default_image_is_bound_to_chart_application_version() -> None:
    chart = yaml.safe_load(CHART.joinpath("Chart.yaml").read_text())
    values = yaml.safe_load(CHART.joinpath("values.yaml").read_text())
    deployment = by_kind(render(), "Deployment")[0]
    image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]

    assert values["image"]["repository"] == "ghcr.io/sangmu1126/kubefit"
    assert image == f'{values["image"]["repository"]}:{chart["appVersion"]}'


def test_opt_in_rbac_is_namespace_scoped_and_read_only() -> None:
    documents = render(
        "--set",
        "rbac.targetNamespaces[0]=demo",
        "--set",
        "rbac.targetNamespaces[1]=staging",
        "--set",
        "serviceAccount.automountToken=true",
    )

    assert not by_kind(documents, "ClusterRole")
    assert not by_kind(documents, "ClusterRoleBinding")
    roles = by_kind(documents, "Role")
    bindings = by_kind(documents, "RoleBinding")
    assert {role["metadata"]["namespace"] for role in roles} == {"demo", "staging"}
    assert {binding["metadata"]["namespace"] for binding in bindings} == {
        "demo",
        "staging",
    }
    expected_rules = [
        {"apiGroups": ["apps"], "resources": ["deployments"], "verbs": ["get"]},
        {"apiGroups": ["apps"], "resources": ["replicasets"], "verbs": ["list"]},
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["list"]},
    ]
    for role in roles:
        assert role["rules"] == expected_rules
    for binding in bindings:
        assert binding["roleRef"]["kind"] == "Role"
        assert binding["subjects"] == [
            {
                "kind": "ServiceAccount",
                "name": "test-kubefit",
                "namespace": "kubefit-system",
            }
        ]


@pytest.mark.parametrize(
    "extra_args",
    [
        ("--set", "rbac.targetNamespaces[0]=demo"),
        (
            "--set",
            "rbac.targetNamespaces[0]=demo",
            "--set",
            "serviceAccount.automountToken=true",
            "--set",
            "rbac.create=false",
        ),
        (
            "--set",
            "rbac.targetNamespaces[0]=demo",
            "--set",
            "serviceAccount.automountToken=true",
            "--set",
            "serviceAccount.create=false",
        ),
    ],
)
def test_chart_rejects_incomplete_observation_identity(extra_args: tuple[str, ...]) -> None:
    result = helm(
        "template",
        "test",
        str(CHART),
        "--namespace",
        "kubefit-system",
        *extra_args,
        check=False,
    )

    assert result.returncode != 0
    assert "Error: execution error" in result.stderr


def test_chart_schema_rejects_invalid_namespace() -> None:
    result = helm(
        "template",
        "test",
        str(CHART),
        "--set",
        "rbac.targetNamespaces[0]=Bad_Namespace",
        check=False,
    )

    assert result.returncode != 0
    assert "/rbac/targetNamespaces/0" in result.stderr


def test_container_image_runs_as_numeric_non_root_user() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    node_base = (
        "node:24-alpine@sha256:"
        "d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43"
    )
    python_base = (
        "python:3.14-slim@sha256:"
        "ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4"
    )
    assert f"FROM {node_base} AS dashboard-builder" in dockerfile
    assert "RUN npm ci --ignore-scripts" in dockerfile
    assert "RUN npm run build" in dockerfile
    assert f"FROM {python_base} AS builder" in dockerfile
    assert f"FROM {python_base} AS runtime" in dockerfile
    assert dockerfile.count("@sha256:") == 3
    assert "COPY requirements ./requirements" in dockerfile
    assert dockerfile.count("--require-hashes") == 2
    assert "--requirement requirements/build.lock" in dockerfile
    assert "--requirement requirements/runtime.lock" in dockerfile
    assert "--no-deps --no-build-isolation" in dockerfile
    assert "KUBEFIT_DASHBOARD_DIRECTORY=/opt/kubefit/dashboard" in dockerfile
    assert "COPY --from=dashboard-builder --chown=10001:10001" in dockerfile
    assert "--no-index --find-links=/wheels" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["uvicorn"]' in dockerfile
    assert "COPY tests" not in dockerfile


def test_docker_context_excludes_generated_dashboard_files() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()

    assert "**/node_modules" in dockerignore
    assert "**/dist" in dockerignore
    assert "**/*.tsbuildinfo" in dockerignore
