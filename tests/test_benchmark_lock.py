import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks import BenchmarkExecutionLock, BenchmarkLockError


def execution_lock(root: Path, context: str = "kind-kubefit") -> BenchmarkExecutionLock:
    return BenchmarkExecutionLock(
        root=root,
        context=context,
        namespace="demo",
        deployment="api",
    )


def test_blocks_same_target_until_owner_releases(tmp_path: Path) -> None:
    root = tmp_path / "locks"

    with execution_lock(root) as first:
        assert first.path.is_file()
        with pytest.raises(BenchmarkLockError, match="already running"):
            with execution_lock(root):
                pass

    with execution_lock(root):
        pass


def test_blocks_same_target_in_another_process(tmp_path: Path) -> None:
    root = tmp_path / "locks"
    script = """
from pathlib import Path
import sys
from benchmarks import BenchmarkExecutionLock, BenchmarkLockError
try:
    with BenchmarkExecutionLock(Path(sys.argv[1]), 'kind-kubefit', 'demo', 'api'):
        raise SystemExit(0)
except BenchmarkLockError:
    raise SystemExit(23)
"""

    with execution_lock(root):
        completed = subprocess.run(
            [sys.executable, "-c", script, str(root)],
            check=False,
            capture_output=True,
            text=True,
        )

    assert completed.returncode == 23


def test_releases_lock_when_execution_raises(tmp_path: Path) -> None:
    root = tmp_path / "locks"

    with pytest.raises(RuntimeError, match="execution failed"):
        with execution_lock(root):
            raise RuntimeError("execution failed")

    with execution_lock(root):
        pass


def test_different_context_has_independent_lock(tmp_path: Path) -> None:
    root = tmp_path / "locks"

    with execution_lock(root, "kind-first"), execution_lock(root, "kind-second"):
        pass


def test_rejects_symlinked_lock_root(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(BenchmarkLockError, match="root must not be a symlink"):
        execution_lock(linked)


def test_rejects_symlinked_lock_file(tmp_path: Path) -> None:
    lock = execution_lock(tmp_path / "locks")
    target = tmp_path / "target"
    target.write_text("")
    lock.path.symlink_to(target)

    with pytest.raises(BenchmarkLockError, match="must not be a symlink"):
        with lock:
            pass


def test_rejects_reentering_same_lock_object(tmp_path: Path) -> None:
    lock = execution_lock(tmp_path / "locks")

    with lock, pytest.raises(BenchmarkLockError, match="already acquired"):
        lock.__enter__()
