import json

import pytest

from collector.kubernetes import (
    KubectlDeploymentCollector,
    KubernetesCollectionError,
)

DEPLOYMENT = {
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


def test_collects_deployment_resources_and_pods() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str]) -> str:
        commands.append(command)
        if "deployment" in command:
            return json.dumps(DEPLOYMENT)
        return json.dumps({"items": [{"metadata": {"name": "demo-abc"}}]})

    result = KubectlDeploymentCollector(runner=runner).collect("demo", "api")

    assert result.resources.cpu_request_millicores == 1000
    assert result.resources.memory_request_mib == 2048
    assert result.pods == ["demo-abc"]
    assert result.desired_replicas == 1
    assert result.available_replicas == 1
    assert ["-l", "app=demo"] == commands[1][commands[1].index("-l") : commands[1].index("-l") + 2]


def test_rejects_missing_resource_configuration() -> None:
    document = json.loads(json.dumps(DEPLOYMENT))
    del document["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]

    with pytest.raises(KubernetesCollectionError, match="must define"):
        KubectlDeploymentCollector(runner=lambda _: json.dumps(document)).collect("demo", "api")
