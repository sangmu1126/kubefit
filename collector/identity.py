import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class IdentitySnapshotError(RuntimeError):
    """Raised when a workload identity snapshot cannot be trusted or stored."""


@dataclass(frozen=True)
class WorkloadIdentity:
    namespace: str
    name: str
    uid: str
    created_at: datetime
    replica_sets: tuple[str, ...]


class IdentitySnapshotStore:
    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path

    def remember(
        self,
        *,
        namespace: str,
        name: str,
        uid: str,
        created_at: datetime,
        replica_sets: list[str],
    ) -> WorkloadIdentity:
        if not replica_sets:
            raise ValueError("at least one ReplicaSet is required")

        document = self._load()
        key = f"{namespace}/{name}"
        created_at_value = created_at.isoformat()
        existing = document["workloads"].get(key)

        if existing is not None and existing["uid"] == uid:
            if existing["created_at"] != created_at_value:
                raise IdentitySnapshotError(
                    "snapshot creation time conflicts with the current Deployment UID"
                )
            merged_replica_sets = sorted(set(existing["replica_sets"]) | set(replica_sets))
        else:
            merged_replica_sets = sorted(set(replica_sets))

        record = {
            "namespace": namespace,
            "name": name,
            "uid": uid,
            "created_at": created_at_value,
            "replica_sets": merged_replica_sets,
        }
        document["workloads"][key] = record
        self._write(document)
        return WorkloadIdentity(
            namespace=namespace,
            name=name,
            uid=uid,
            created_at=created_at,
            replica_sets=tuple(merged_replica_sets),
        )

    def _load(self) -> dict:
        if not self._path.exists():
            return {"schema_version": self.SCHEMA_VERSION, "workloads": {}}
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IdentitySnapshotError(f"cannot read identity snapshot: {exc}") from exc
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != self.SCHEMA_VERSION
            or not isinstance(document.get("workloads"), dict)
        ):
            raise IdentitySnapshotError("identity snapshot has an unsupported schema")
        for record in document["workloads"].values():
            if not _valid_record(record):
                raise IdentitySnapshotError("identity snapshot contains an invalid workload record")
        return document

    def _write(self, document: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                delete=False,
            ) as temporary:
                temporary_path = temporary.name
                json.dump(document, temporary, indent=2, sort_keys=True)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self._path)
        except OSError as exc:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
            raise IdentitySnapshotError(f"cannot write identity snapshot: {exc}") from exc


def _valid_record(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    return (
        all(isinstance(record.get(field), str) for field in ("namespace", "name", "uid"))
        and isinstance(record.get("created_at"), str)
        and isinstance(record.get("replica_sets"), list)
        and all(isinstance(item, str) for item in record["replica_sets"])
    )
