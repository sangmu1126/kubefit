from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_local_prometheus_uses_bounded_persistent_storage() -> None:
    values = yaml.safe_load((ROOT / "deploy/local/prometheus-values.yaml").read_text())
    spec = values["prometheus"]["prometheusSpec"]
    claim = spec["storageSpec"]["volumeClaimTemplate"]["spec"]

    assert spec["retention"] == "2d"
    assert spec["retentionSize"] == "4GB"
    assert claim == {
        "storageClassName": "standard",
        "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": "5Gi"}},
    }


def test_local_setup_names_kind_context_for_every_cluster_mutation() -> None:
    script = (ROOT / "deploy/local/up.sh").read_text()

    assert "kubectl config use-context" not in script
    assert 'helm upgrade --install monitoring' in script
    assert '--kube-context "${cluster_context}"' in script
    assert script.count('kubectl --context "${cluster_context}"') == 2
