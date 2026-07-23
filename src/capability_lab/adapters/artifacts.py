from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from capability_lab.domain.models import ArtifactPayload, ArtifactRef


class ArtifactError(RuntimeError):
    pass


class FilesystemArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, artifact: ArtifactPayload) -> ArtifactRef:
        if Path(artifact.name).name != artifact.name or artifact.name in {"", ".", ".."}:
            raise ArtifactError(f"invalid artifact name: {artifact.name!r}")
        digest = hashlib.sha256(artifact.content).hexdigest()
        blob = self.root / "blobs" / "sha256" / digest[:2] / digest
        run_path = self.root / "runs" / artifact.run_id / artifact.name
        blob.parent.mkdir(parents=True, exist_ok=True)
        run_path.parent.mkdir(parents=True, exist_ok=True)
        if run_path.exists() and run_path.read_bytes() != artifact.content:
            raise ArtifactError(
                f"artifact {artifact.run_id}/{artifact.name} already exists with different content"
            )
        if blob.exists():
            if blob.read_bytes() != artifact.content:
                raise ArtifactError(f"content-addressed blob is corrupt: {digest}")
        else:
            with tempfile.NamedTemporaryFile(dir=blob.parent, delete=False) as temporary:
                temporary.write(artifact.content)
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, blob)
            blob.chmod(0o444)
        if not run_path.exists():
            try:
                with run_path.open("xb") as run_file:
                    run_file.write(artifact.content)
            except FileExistsError:
                if run_path.read_bytes() != artifact.content:
                    raise ArtifactError(
                        f"artifact {artifact.run_id}/{artifact.name} already exists "
                        "with different content"
                    ) from None
        return ArtifactRef(
            sha256=digest,
            size=len(artifact.content),
            blob_path=str(blob),
            run_path=str(run_path),
        )

    def open(self, ref: ArtifactRef) -> BinaryIO:
        blob = Path(ref.blob_path).open("rb")  # noqa: SIM115 - caller owns returned handle
        content = blob.read()
        if len(content) != ref.size or hashlib.sha256(content).hexdigest() != ref.sha256:
            blob.close()
            raise ArtifactError(f"content-addressed blob is corrupt: {ref.sha256}")
        blob.seek(0)
        return blob
