import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/local/verify-kubefit-chart.sh"


def test_chart_verification_script_has_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_chart_verification_script_is_kind_scoped_and_restores_defaults() -> None:
    script = SCRIPT.read_text()

    assert 'cluster_context="kind-${cluster_name}"' in script
    assert "kubectl config use-context" not in script
    assert script.count('--context "${cluster_context}"') == 3
    assert script.count('--kube-context "${cluster_context}"') >= 2
    assert "trap cleanup EXIT" in script
    assert "restore_needed=true" in script
    assert "serviceAccount.automountToken=false" in script
    assert script.count('--set "fullnameOverride=${release_name}"') == 2
    assert "--reset-values" in script
    assert "command_status=0" in script
    assert "command_status > 1" in script
    assert "kind delete" not in script
    assert "docker push" not in script


def test_chart_verification_script_checks_allow_and_deny_matrix() -> None:
    script = SCRIPT.read_text()

    expected_checks = [
        'assert_can_i yes get "deployment/overprovisioned-api"',
        "assert_can_i yes list replicasets",
        "assert_can_i yes list pods",
        "assert_can_i no watch pods",
        "assert_can_i no get secrets",
        'assert_can_i no update "deployment/overprovisioned-api"',
        "assert_can_i no list pods monitoring",
    ]
    for check in expected_checks:
        assert check in script
    assert script.count(
        'assert_can_i no get "deployment/overprovisioned-api"'
    ) == 2
