from pathlib import Path, PurePath

from gitops.manifest import ManifestSource


class ManifestSourceError(RuntimeError):
    """Raised when repository manifest inputs cross the allowed source boundary."""


def load_manifest_sources(
    repository_root: Path,
    manifest_paths: list[Path],
) -> list[ManifestSource]:
    if repository_root.is_symlink():
        raise ManifestSourceError("repository root must not be a symlink")
    if not repository_root.is_dir():
        raise ManifestSourceError("repository root must be an existing directory")
    if not manifest_paths:
        raise ManifestSourceError("at least one manifest path is required")
    root = repository_root.resolve()
    sources: list[ManifestSource] = []
    seen: set[Path] = set()
    for supplied_path in manifest_paths:
        if ".." in PurePath(supplied_path).parts:
            raise ManifestSourceError(
                f"manifest path must not contain traversal: {supplied_path}"
            )
        candidate = supplied_path if supplied_path.is_absolute() else root / supplied_path
        try:
            lexical_relative = candidate.relative_to(root)
            _reject_symlink_components(root, lexical_relative)
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(root)
        except (FileNotFoundError, ValueError) as exc:
            raise ManifestSourceError(
                f"manifest must exist inside repository root: {supplied_path}"
            ) from exc
        if resolved in seen:
            raise ManifestSourceError(f"duplicate manifest input: {supplied_path}")
        if not resolved.is_file():
            raise ManifestSourceError(f"manifest must be a regular file: {supplied_path}")
        try:
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ManifestSourceError(f"manifest is not readable UTF-8: {supplied_path}") from exc
        seen.add(resolved)
        sources.append(ManifestSource(path=relative.as_posix(), content=content))
    return sources


def _reject_symlink_components(root: Path, relative: Path) -> None:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ManifestSourceError(f"manifest path contains a symlink: {relative}")
