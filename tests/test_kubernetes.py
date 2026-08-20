import json
from datetime import UTC, datetime

import pytest

from collector.kubernetes import (
    KubectlDeploymentCollector,
    KubernetesCollectionError,
)

DEPLOYMENT = {
    "metadata": {
        "uid": "deployment-uid",
        "creationTimestamp": "2026-08-20T00:00:00Z",
    },
    "status": {"availableReplicas": 1},
    "spec": {
        "replicas": 1,
        "selector": {"matchLabels": {"app": "demo"}},
        "template": {
            "spec": {
                "containers": [
                    {
                        "name": "api",
                        "resources": {
                            "requests": {"cpu": "1", "memory": "2Gi"},
                            "limits": {"cpu": "2000m", "memory": "4096Mi"},
                        },
                    }
                ]
            }
        },
    }
}

REPLICA_SETS = {
    "items": [
        {
            "metadata": {
                "name": "demo-owned",
                "ownerReferences": [
                    {
                        "controller": True,
                        "kind": "Deployment",
                        "uid": "deployment-uid",
                    }
                ],
            }
        },
        {
            "metadata": {
                "name": "demo-foreign",
                "ownerReferences": [
                    {
                        "controller": True,
                        "kind": "Deployment",
                        "uid": "different-uid",
                    }
                ],
            }
        },
    ]
}


def test_collects_deployment_resources_and_pods() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str]) -> str:
        commands.append(command)
        if "deployment" in command:
            return json.dumps(DEPLOYMENT)
        if "replicasets" in command:
            return json.dumps(REPLICA_SETS)
        return json.dumps({"items": [{"metadata": {"name": "demo-abc"}}]})

    result = KubectlDeploymentCollector(runner=runner).collect("demo", "api")

    assert result.resources.cpu_request_millicores == 1000
    assert result.resources.memory_request_mib == 2048
    assert result.pods == ["demo-abc"]
    assert result.replica_sets == ["demo-owned"]
    assert result.uid == "deployment-uid"
    assert result.created_at == datetime(2026, 8, 20, tzinfo=UTC)
    assert result.desired_replicas == 1
    assert result.available_replicas == 1
    assert ["-l", "app=demo"] == commands[1][
        commands[1].index("-l") : commands[1].index("-l") + 2
    ]


def test_rejects_missing_resource_configuration() -> None:
    document = json.loads(json.dumps(DEPLOYMENT))
    del document["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]

    with pytest.raises(KubernetesCollectionError, match="must define"):
        KubectlDeploymentCollector(runner=lambda _: json.dumps(document)).collect("demo", "api")


def test_rejects_when_no_replicaset_matches_current_deployment_uid() -> None:
    def runner(command: list[str]) -> str:
        if "deployment" in command:
            return json.dumps(DEPLOYMENT)
        if "replicasets" in command:
            return json.dumps({"items": []})
        return json.dumps({"items": [{"metadata": {"name": "demo-abc"}}]})

    with pytest.raises(KubernetesCollectionError, match="current UID"):
        KubectlDeploymentCollector(runner=runner).collect("demo", "api")
