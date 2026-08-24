import re
from pathlib import Path

import yaml

WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "release-packages.yml"
FULL_COMMIT_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")


def load_workflow() -> dict[str, object]:
    return yaml.load(WORKFLOW_PATH.read_text(), Loader=yaml.BaseLoader)


def commands(job: dict[str, object]) -> list[str]:
    return [step["run"] for step in job["steps"] if "run" in step]


def test_release_requires_a_tag_or_explicit_existing_tag_input() -> None:
    workflow = load_workflow()

    assert workflow["on"] == {
        "push": {"tags": ["v*.*.*"]},
        "workflow_dispatch": {
            "inputs": {
                "release_tag": {
                    "description": "Existing annotated vMAJOR.MINOR.PATCH tag to publish",
                    "required": "true",
                    "type": "string",
                }
            }
        },
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"


def test_release_pins_actions_and_bounds_jobs() -> None:
    jobs = load_workflow()["jobs"]

    assert set(jobs) == {
        "verify",
        "publish-image",
        "publish-chart",
        "verify-public",
    }
    assert {name: job["timeout-minutes"] for name, job in jobs.items()} == {
        "verify": "5",
        "publish-image": "30",
        "publish-chart": "10",
        "verify-public": "10",
    }
    action_references = [
        step["uses"]
        for job in jobs.values()
        for step in job["steps"]
        if "uses" in step
    ]
    assert action_references
    assert all(FULL_COMMIT_ACTION.fullmatch(reference) for reference in action_references)


def test_release_verifies_identity_and_all_version_sources() -> None:
    verify_command = commands(load_workflow()["jobs"]["verify"])[0]

    assert "^v([0-9]+)\\.([0-9]+)\\.([0-9]+)$" in verify_command
    assert "git cat-file -t" in verify_command
    assert 'git merge-base --is-ancestor "${release_commit}" origin/main' in verify_command
    assert 'tomllib.load(open("pyproject.toml", "rb"))' in verify_command
    assert 'deploy/helm/kubefit/Chart.yaml' in verify_command
    assert "project_version" in verify_command
    assert "chart_version" in verify_command
    assert "app_version" in verify_command


def test_image_is_multi_architecture_source_bound_and_attested() -> None:
    job = load_workflow()["jobs"]["publish-image"]
    build = next(step for step in job["steps"] if step.get("id") == "build")

    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert build["with"]["platforms"] == "linux/amd64,linux/arm64"
    assert build["with"]["push"] == "true"
    assert build["with"]["provenance"] == "mode=max"
    assert build["with"]["sbom"] == "true"
    assert ":${{ needs.verify.outputs.version }}" in build["with"]["tags"]
    assert ":sha-${{ needs.verify.outputs.commit }}" in build["with"]["tags"]
    assert ":latest" not in build["with"]["tags"]
    assert (
        "org.opencontainers.image.revision=${{ needs.verify.outputs.commit }}"
        in build["with"]["labels"]
    )


def test_chart_and_public_verification_use_separate_auth_boundaries() -> None:
    jobs = load_workflow()["jobs"]
    chart_commands = commands(jobs["publish-chart"])
    public_commands = commands(jobs["verify-public"])

    assert jobs["publish-chart"]["permissions"] == {
        "contents": "read",
        "packages": "write",
    }
    assert any("helm package deploy/helm/kubefit" in command for command in chart_commands)
    assert any("helm push" in command and "oci://ghcr.io/" in command for command in chart_commands)
    assert jobs["verify-public"]["permissions"] == {"contents": "read"}
    assert not any("login" in command for command in public_commands)
    assert any("EXPECTED_DIGEST" in command for command in public_commands)
    assert any("docker pull" in command for command in public_commands)
    assert any("verify-image-runtime.sh" in command for command in public_commands)
    assert any("helm pull" in command for command in public_commands)
