import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_repository_contains_the_complete_apache_2_license() -> None:
    license_text = ROOT.joinpath("LICENSE").read_text()

    assert "Apache License\n                           Version 2.0, January 2004" in license_text
    assert "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION" in license_text
    for section in range(1, 10):
        assert f"   {section}. " in license_text
    assert "END OF TERMS AND CONDITIONS" in license_text
    assert "APPENDIX: How to apply the Apache License to your work." in license_text


def test_python_and_container_packages_include_the_root_license() -> None:
    project = tomllib.loads(ROOT.joinpath("pyproject.toml").read_text())["project"]
    dockerfile = ROOT.joinpath("Dockerfile").read_text()

    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert "COPY pyproject.toml README.md LICENSE ./" in dockerfile
