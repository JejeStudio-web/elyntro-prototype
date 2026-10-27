import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from artifact_store import ArtifactStore


def test_artifact_versions_are_immutable_and_restore_appends(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts.db")
    first = store.save(namespace="user-a", name="notes.md", content="# one")
    second = store.save(namespace="user-a", name="notes.md", content="# two",
                        artifact_id=first.artifact_id, project_id="project-1")
    assert first.version == 1 and second.version == 2
    assert store.get(namespace="user-a", artifact_id=first.artifact_id, version=1).content == b"# one"
    restored = store.restore(namespace="user-a", artifact_id=first.artifact_id, version=1)
    assert restored.version == 3
    assert store.get(namespace="user-a", artifact_id=first.artifact_id).content == b"# one"
    assert restored.metadata["restored_from_version"] == 1


def test_artifact_namespace_isolation(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts.db")
    item = store.save(namespace="user-a", name="private.txt", content="secret")
    with pytest.raises(KeyError):
        store.get(namespace="user-b", artifact_id=item.artifact_id)
    with pytest.raises(KeyError):
        store.save(namespace="user-b", name="overwrite.txt", content="x", artifact_id=item.artifact_id)


def test_artifact_validates_limits_and_metadata(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts.db", max_bytes=16)
    with pytest.raises(ValueError):
        store.save(namespace="u", name="x", content=b"x" * 17)
    with pytest.raises(ValueError):
        store.save(namespace="u", name="x", content="x", metadata={"large": "x" * 20000})
    with pytest.raises(ValueError):
        store.list(namespace="u", limit=0)


def test_artifact_listing_filters_project_and_returns_latest(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts.db")
    item = store.save(namespace="u", name="plan.md", content="v1", project_id="p")
    store.save(namespace="u", name="plan.md", content="v2", artifact_id=item.artifact_id, project_id="p")
    store.save(namespace="u", name="other.md", content="other", project_id="q")
    rows = store.list(namespace="u", project_id="p")
    assert len(rows) == 1
    assert rows[0]["version"] == 2
    assert rows[0]["size_bytes"] == 2
