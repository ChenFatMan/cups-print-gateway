from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredFile:
    path: Path
    relative_path: str
    size_bytes: int
    sha256: str


class TaskStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def task_dir(self, task_id: int) -> Path:
        return self.root / "tasks" / str(task_id)

    def original_dir(self, task_id: int) -> Path:
        return self.task_dir(task_id) / "original"

    def converted_dir(self, task_id: int) -> Path:
        return self.task_dir(task_id) / "converted"

    def previews_dir(self, task_id: int) -> Path:
        return self.task_dir(task_id) / "previews"

    def logs_dir(self, task_id: int) -> Path:
        return self.task_dir(task_id) / "logs"

    def save_bytes(self, *, task_id: int, folder: str, filename: str, content: bytes) -> StoredFile:
        target_dir = self._folder(task_id, folder)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        target.write_bytes(content)
        return self.describe(target)

    def resolve_relative(self, relative_path: str) -> Path:
        root = self.root.resolve()
        target = (self.root / relative_path).resolve()
        if root != target and root not in target.parents:
            raise ValueError("storage path escapes root")
        return target

    def describe(self, path: Path) -> StoredFile:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        relative = path.resolve().relative_to(self.root.resolve())
        return StoredFile(
            path=path,
            relative_path=str(relative),
            size_bytes=path.stat().st_size,
            sha256=digest.hexdigest(),
        )

    def cleanup_task_files(self, task_id: int) -> None:
        task_dir = self.task_dir(task_id).resolve()
        root = self.root.resolve()
        if root != task_dir and root not in task_dir.parents:
            raise ValueError("task directory escapes root")
        if task_dir.exists():
            shutil.rmtree(task_dir)

    def _folder(self, task_id: int, folder: str) -> Path:
        allowed = {
            "original": self.original_dir(task_id),
            "converted": self.converted_dir(task_id),
            "previews": self.previews_dir(task_id),
            "logs": self.logs_dir(task_id),
        }
        if folder not in allowed:
            raise ValueError(f"unsupported task folder: {folder}")
        return allowed[folder]
