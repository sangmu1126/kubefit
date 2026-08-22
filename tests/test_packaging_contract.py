import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def dependency_name(specifier: str) -> str:
    return specifier.split("<", 1)[0].split(">", 1)[0]


def test_prometheus_http_client_is_a_runtime_dependency() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

    runtime_names = {dependency_name(item) for item in project["dependencies"]}
    dev_names = {
        dependency_name(item) for item in project["optional-dependencies"]["dev"]
    }

    assert "httpx" in runtime_names
    assert "httpx" not in dev_names
