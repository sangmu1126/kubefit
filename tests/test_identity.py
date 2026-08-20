import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from collector.identity import IdentitySnapshotError, IdentitySnapshotStore


def test_merges_replicasets_for_the_same_deployment_uid(tmp_path: Path) -> None:
    path = tmp_path / "identities.json"
    store = IdentitySnapshotStore(path)
    created_at = datetime(2026, 8, 21, tzinfo=UTC)

    store.remember(
        namespace="demo",
        name="api",
        uid="uid-1",
        created_at=created_at,
        replica_sets=["api-old"],
    )
    identity = store.remember(
        namespace="demo",
        name="api",
        uid="uid-1",
        created_at=created_at,
        replica_sets=["api-current"],
    )

    assert identity.replica_sets == ("api-current", "api-old")
    document = json.loads(path.read_text())
    assert document["workloads"]["demo/api"]["replica_sets"] == [
        "api-current",
        "api-old",
    ]


def test_replaces_history_when_the_deployment_uid_changes(tmp_path: Path) -> None:
    path = tmp_path / "identities.json"
    store = IdentitySnapshotStore(path)
    first_created_at = datetime(2026, 8, 21, tzinfo=UTC)

    store.remember(
        namespace="demo",
        name="api",
        uid="uid-1",
        created_at=first_created_at,
        replica_sets=["api-old"],
    )
    identity = store.remember(
        namespace="demo",
        name="api",
        uid="uid-2",
        created_at=first_created_at + timedelta(hours=1),
        replica_sets=["api-new"],
    )

    assert identity.uid == "uid-2"
    assert identity.replica_sets == ("api-new",)


def test_rejects_conflicting_creation_time_for_the_same_uid(tmp_path: Path) -> None:
    path = tmp_path / "identities.json"
    store = IdentitySnapshotStore(path)
    created_at = datetime(2026, 8, 21, tzinfo=UTC)
    store.remember(
        namespace="demo",
        name="api",
        uid="uid-1",
        created_at=created_at,
        replica_sets=["api-old"],
    )

    with pytest.raises(IdentitySnapshotError, match="creation time conflicts"):
        store.remember(
            namespace="demo",
            name="api",
            uid="uid-1",
            created_at=created_at + timedelta(seconds=1),
            replica_sets=["api-current"],
        )


def test_rejects_unsupported_snapshot_schema(tmp_path: Path) -> None:
    path = tmp_path / "identities.json"
    path.write_text('{"schema_version": 999, "workloads": {}}')

    with pytest.raises(IdentitySnapshotError, match="unsupported schema"):
        IdentitySnapshotStore(path).remember(
            namespace="demo",
            name="api",
            uid="uid-1",
            created_at=datetime(2026, 8, 21, tzinfo=UTC),
            replica_sets=["api-current"],
        )
