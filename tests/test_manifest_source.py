from pathlib import Path

import pytest

from gitops import ManifestSourceError, load_manifest_sources


def test_loads_ordered_repository_relative_sources(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "deploy").mkdir(parents=True)
    first = root / "deploy" / "first.yaml"
    second = root / "deploy" / "second.yaml"
    first.write_text("kind: Service\n")
    second.write_text("kind: Deployment\n")

    sources = load_manifest_sources(root, [Path("deploy/first.yaml"), second])

    assert [source.path for source in sources] == [
        "deploy/first.yaml",
        "deploy/second.yaml",
    ]
    assert [source.content for source in sources] == [
        "kind: Service\n",
        "kind: Deployment\n",
    ]


@pytest.mark.parametrize("supplied", [Path("../outside.yaml"), Path("missing.yaml")])
def test_rejects_traversal_or_missing_source(tmp_path: Path, supplied: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "outside.yaml").write_text("kind: Service\n")

    with pytest.raises(ManifestSourceError, match="manifest"):
        load_manifest_sources(root, [supplied])


def test_rejects_absolute_source_outside_repository(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("kind: Service\n")

    with pytest.raises(ManifestSourceError, match="inside repository"):
        load_manifest_sources(root, [outside])


def test_rejects_duplicate_source_identity(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "demo.yaml"
    source.write_text("kind: Deployment\n")

    with pytest.raises(ManifestSourceError, match="duplicate"):
        load_manifest_sources(root, [Path("demo.yaml"), source])


def test_rejects_symlinked_repository_root(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(ManifestSourceError, match="root must not be a symlink"):
        load_manifest_sources(linked, [Path("demo.yaml")])


def test_rejects_symlink_inside_repository(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target.yaml"
    target.write_text("kind: Deployment\n")
    linked = root / "linked.yaml"
    linked.symlink_to(target)

    with pytest.raises(ManifestSourceError, match="contains a symlink"):
        load_manifest_sources(root, [Path("linked.yaml")])


def test_rejects_directory_as_manifest(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "deploy").mkdir(parents=True)

    with pytest.raises(ManifestSourceError, match="regular file"):
        load_manifest_sources(root, [Path("deploy")])


def test_rejects_non_utf8_manifest(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "invalid.yaml").write_bytes(b"\xff\xfe")

    with pytest.raises(ManifestSourceError, match="UTF-8"):
        load_manifest_sources(root, [Path("invalid.yaml")])
