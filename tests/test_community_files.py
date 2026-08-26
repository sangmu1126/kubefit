from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_required_community_files_are_present_and_discoverable() -> None:
    required = {
        "CONTRIBUTING.md": "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md": "CODE_OF_CONDUCT.md",
        "SECURITY.md": "SECURITY.md",
    }
    readme = (ROOT / "README.md").read_text()

    for path, link in required.items():
        assert (ROOT / path).is_file()
        assert f"]({link})" in readme


def test_issue_forms_are_structured_and_have_unique_ids() -> None:
    forms = ROOT / ".github" / "ISSUE_TEMPLATE"

    for filename in ("bug_report.yml", "feature_request.yml"):
        form = yaml.safe_load((forms / filename).read_text())
        assert form["name"]
        assert form["description"]
        assert form["body"]
        ids = [item["id"] for item in form["body"] if "id" in item]
        assert len(ids) == len(set(ids))

    config = yaml.safe_load((forms / "config.yml").read_text())
    assert config["contact_links"][0]["name"] == "Security report"
    assert config["contact_links"][0]["url"].startswith("https://")


def test_pull_request_template_preserves_project_safety_boundaries() -> None:
    template = (ROOT / ".github" / "pull_request_template.md").read_text()

    for expected in (
        "No credentials",
        "read-only",
        "restores the original workload",
        "Draft-only",
        "Development-journal entry",
    ):
        assert expected in template
