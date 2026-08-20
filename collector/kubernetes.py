import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from recommender import CurrentResources


class KubernetesCollectionError(RuntimeError):
    """Raised when workload metadata cannot be collected through kubectl."""


@dataclass(frozen=True)
class DeploymentResources:
    namespace: str
    name: str
    container: str
    pods: list[str]
    desired_replicas: int
    available_replicas: int
    resources: CurrentResources


Runner = Callable[[Sequence[str]], str]


def _run(command: Sequence[str]) -> str:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise KubernetesCollectionError(f"kubectl failed: {detail.strip()}") from exc
    return result.stdout


def _cpu_millicores(value: str) -> int:
    if value.endswith("m"):
        return int(value[:-1])
    return int(float(value) * 1000)


def _memory_mib(value: str) -> int:
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024}
    for suffix, multiplier in units.items():
        if value.endswith(suffix):
            return round(float(value[: -len(suffix)]) * multiplier)
    return round(float(value) / (1024 * 1024))


class KubectlDeploymentCollector:
    def __init__(self, runner: Runner = _run, context: str | None = None) -> None:
        self._runner = runner
        self._context = context

    def _command(self, *args: str) -> list[str]:
        command = ["kubectl"]
        if self._context:
            command.extend(["--context", self._context])
        command.extend(args)
        return command

    def collect(
        self, namespace: str, deployment: str, container_name: str | None = None
    ) -> DeploymentResources:
        raw = self._runner(
            self._command("get", "deployment", deployment, "-n", namespace, "-o", "json")
        )
        document = json.loads(raw)
        containers = document["spec"]["template"]["spec"]["containers"]
        if container_name is None and len(containers) != 1:
            names = ", ".join(item["name"] for item in containers)
            raise KubernetesCollectionError(f"select a container from: {names}")
        container = next(
            (item for item in containers if item["name"] == container_name),
            containers[0] if container_name is None else None,
        )
        if container is None:
            raise KubernetesCollectionError(f"container {container_name!r} was not found")
        resources = container.get("resources", {})
        requests = resources.get("requests", {})
        limits = resources.get("limits", {})
        required = [
            requests.get("cpu"),
            limits.get("cpu"),
            requests.get("memory"),
            limits.get("memory"),
        ]
        if any(value is None for value in required):
            raise KubernetesCollectionError(
                "container must define CPU and memory requests and limits"
            )

        selector = document["spec"]["selector"]["matchLabels"]
        label_selector = ",".join(f"{key}={value}" for key, value in sorted(selector.items()))
        pods_raw = self._runner(
            self._command("get", "pods", "-n", namespace, "-l", label_selector, "-o", "json")
        )
        pods = [item["metadata"]["name"] for item in json.loads(pods_raw)["items"]]
        if not pods:
            raise KubernetesCollectionError("deployment has no matching pods")
        return DeploymentResources(
            namespace=namespace,
            name=deployment,
            container=container["name"],
            pods=pods,
            desired_replicas=document["spec"].get("replicas", 1),
            available_replicas=document.get("status", {}).get("availableReplicas", 0),
            resources=CurrentResources(
                cpu_request_millicores=_cpu_millicores(requests["cpu"]),
                cpu_limit_millicores=_cpu_millicores(limits["cpu"]),
                memory_request_mib=_memory_mib(requests["memory"]),
                memory_limit_mib=_memory_mib(limits["memory"]),
            ),
        )
