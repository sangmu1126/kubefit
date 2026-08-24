import os
import tomllib
import zipfile
from pathlib import Path

from hatchling.build import build_wheel

ROOT = Path(__file__).resolve().parents[1]
GENERATED_BENCHMARK_DIRECTORIES = (
    "benchmarks/results",
    "benchmarks/pairs",
    "benchmarks/campaigns",
    "benchmarks/campaign-evidence",
)


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


def test_generated_benchmark_data_is_excluded_from_wheel(tmp_path: Path) -> None:
    marker_name = "kubefit-packaging-contract-marker.json"
    markers = [ROOT / directory / marker_name for directory in GENERATED_BENCHMARK_DIRECTORIES]

    for marker in markers:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('{"generated":true}\n')

    previous_directory = Path.cwd()
    try:
        os.chdir(ROOT)
        wheel_name = build_wheel(str(tmp_path))
    finally:
        os.chdir(previous_directory)
        for marker in markers:
            marker.unlink(missing_ok=True)
            try:
                marker.parent.rmdir()
            except OSError:
                pass

    with zipfile.ZipFile(tmp_path / wheel_name) as wheel:
        packaged_files = set(wheel.namelist())

    assert "benchmarks/k6/resource_profile.js" in packaged_files
    assert "benchmarks/k6/observation_profile.js" in packaged_files
    for directory in GENERATED_BENCHMARK_DIRECTORIES:
        assert f"{directory}/{marker_name}" not in packaged_files


def test_generated_benchmark_data_is_excluded_from_docker_context() -> None:
    ignored_paths = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert set(GENERATED_BENCHMARK_DIRECTORIES) <= ignored_paths
