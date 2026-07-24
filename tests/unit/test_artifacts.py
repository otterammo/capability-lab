from pathlib import Path

import pytest

from capability_lab.adapters.artifacts import ArtifactError, FilesystemArtifactStore
from capability_lab.domain.models import ArtifactPayload


def test_artifact_store_deduplicates_content(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)

    first = store.put(ArtifactPayload("run-1", "patch.diff", b"same"))
    second = store.put(ArtifactPayload("run-2", "patch.diff", b"same"))

    assert first.sha256 == second.sha256
    assert first.blob_path == second.blob_path
    assert first.run_path != second.run_path
    assert store.open(first).read() == b"same"
    assert (tmp_path / "runs/run-1/patch.diff").read_bytes() == b"same"


def test_run_artifact_mutation_does_not_change_blob_or_other_runs(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    first = store.put(ArtifactPayload("run-1", "patch.diff", b"same"))
    second = store.put(ArtifactPayload("run-2", "patch.diff", b"same"))

    Path(first.run_path).write_bytes(b"changed")

    assert Path(first.blob_path).read_bytes() == b"same"
    assert Path(second.run_path).read_bytes() == b"same"
    assert Path(first.run_path).stat().st_ino != Path(first.blob_path).stat().st_ino


def test_conflicting_run_artifact_reuse_fails_without_changing_evidence(
    tmp_path: Path,
) -> None:
    store = FilesystemArtifactStore(tmp_path)
    original = store.put(ArtifactPayload("run-1", "result.json", b"original"))

    with pytest.raises(ArtifactError, match="already exists with different content"):
        store.put(ArtifactPayload("run-1", "result.json", b"replacement"))

    assert Path(original.run_path).read_bytes() == b"original"
    assert Path(original.blob_path).read_bytes() == b"original"
    assert len(list((tmp_path / "blobs/sha256").glob("*/*"))) == 1


def test_open_rejects_corrupt_blob(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    artifact = store.put(ArtifactPayload("run-1", "result.json", b"same"))
    blob = Path(artifact.blob_path)
    blob.chmod(0o600)
    blob.write_bytes(b"evil")

    with pytest.raises(ArtifactError, match="content-addressed blob is corrupt"):
        store.open(artifact)
