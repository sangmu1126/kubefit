import re
from pathlib import Path

import yaml

WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
FULL_COMMIT_ACTION = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_-]+@[0-9a-f]{40}$")


def load_workflow() -> dict[str, object]:
    return yaml.load(WORKFLOW_PATH.read_text(), Loader=yaml.BaseLoader)


def test_ci_uses_read_only_permissions_and_bounded_concurrency() -> None:
    workflow = load_workflow()

    assert workflow["on"] == {
        "push": {"branches": ["main"]},
        "pull_request": "",
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "ci-${{ github.workflow }}-${{ github.ref }}",
        "cancel-in-progress": "true",
    }


def test_ci_exposes_independent_quality_gates() -> None:
    workflow = load_workflow()
    jobs = workflow["jobs"]

    assert set(jobs) == {"python", "dashboard", "helm", "docker"}
    assert {name: job["timeout-minutes"] for name, job in jobs.items()} == {
        "python": "10",
        "dashboard": "10",
        "helm": "10",
        "docker": "15",
    }


def test_ci_pins_every_external_action_to_a_full_commit() -> None:
    jobs = load_workflow()["jobs"]
    action_references = [
        step["uses"]
        for job in jobs.values()
        for step in job["steps"]
        if "uses" in step
    ]

    assert action_references
    assert all(FULL_COMMIT_ACTION.fullmatch(reference) for reference in action_references)


def test_ci_runs_repository_verification_commands() -> None:
    jobs = load_workflow()["jobs"]
    commands = {
        name: [step["run"] for step in job["steps"] if "run" in step]
        for name, job in jobs.items()
    }

    assert commands["python"] == [
        "python -m pip install --require-hashes --requirement requirements/build.lock",
        "python -m pip install --require-hashes --requirement requirements/dev.lock",
        "python -m pip install --no-deps --no-build-isolation --editable .",
        "python -m pip check",
        "ruff check .",
        "pytest -q",
    ]
    assert commands["dashboard"] == [
        "npm ci --ignore-scripts",
        "npm test",
        "npm run build",
    ]
    assert commands["helm"] == [
        "helm lint deploy/helm/kubefit",
        "helm template kubefit deploy/helm/kubefit --namespace kubefit-system",
    ]
    assert commands["docker"][0] == "docker build --tag kubefit:ci ."
    assert "10001:10001" in commands["docker"][1]
    assert commands["docker"][2] == "deploy/local/verify-image-runtime.sh kubefit:ci"
