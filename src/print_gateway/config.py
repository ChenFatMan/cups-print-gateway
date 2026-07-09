from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path
    storage_root: Path
    agent_token: str
    host: str
    port: int
    lease_seconds: int
    cleanup_success_after_seconds: int
    retain_failed_seconds: int
    max_upload_bytes: int


def get_settings() -> Settings:
    data_dir = Path(os.environ.get("PRINT_GATEWAY_DATA", "data")).resolve()
    return Settings(
        database_path=Path(os.environ.get("PRINT_GATEWAY_DB", data_dir / "gateway.sqlite3")).resolve(),
        storage_root=Path(os.environ.get("PRINT_GATEWAY_STORAGE", data_dir / "storage")).resolve(),
        agent_token=os.environ.get("PRINT_GATEWAY_AGENT_TOKEN", "dev-agent-token"),
        host=os.environ.get("PRINT_GATEWAY_HOST", "127.0.0.1"),
        port=int(os.environ.get("PRINT_GATEWAY_PORT", "8000")),
        lease_seconds=int(os.environ.get("PRINT_GATEWAY_LEASE_SECONDS", "300")),
        cleanup_success_after_seconds=int(os.environ.get("PRINT_GATEWAY_CLEANUP_SUCCESS_SECONDS", "300")),
        retain_failed_seconds=int(os.environ.get("PRINT_GATEWAY_RETAIN_FAILED_SECONDS", "86400")),
        max_upload_bytes=int(os.environ.get("PRINT_GATEWAY_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))),
    )
