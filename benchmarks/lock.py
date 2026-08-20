import errno
import fcntl
import hashlib
import os
import stat
from pathlib import Path


class BenchmarkLockError(RuntimeError):
    """Raised when an exclusive benchmark execution lock cannot be acquired."""


class BenchmarkExecutionLock:
    def __init__(
        self,
        root: Path,
        context: str,
        namespace: str,
        deployment: str,
    ) -> None:
        if not all((context, namespace, deployment)):
            raise ValueError("benchmark lock identity fields must not be empty")
        if root.is_symlink():
            raise BenchmarkLockError("benchmark lock root must not be a symlink")
        if root.exists() and not root.is_dir():
            raise BenchmarkLockError("benchmark lock root must be a directory")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity = "\0".join((context, namespace, deployment)).encode()
        digest = hashlib.sha256(identity).hexdigest()[:32]
        self.path = root / f"deployment-{digest}.lock"
        self._descriptor: int | None = None

    def __enter__(self) -> "BenchmarkExecutionLock":
        if self._descriptor is not None:
            raise BenchmarkLockError("benchmark execution lock is already acquired")
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise BenchmarkLockError("benchmark lock file must not be a symlink") from exc
            raise BenchmarkLockError(f"cannot open benchmark lock: {exc}") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise BenchmarkLockError("benchmark lock must be a regular file")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise BenchmarkLockError(
                "another benchmark is already running for this context and Deployment"
            ) from exc
        except Exception:
            os.close(descriptor)
            raise
        self._descriptor = descriptor
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._descriptor is None:
            return
        try:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self._descriptor)
            self._descriptor = None
