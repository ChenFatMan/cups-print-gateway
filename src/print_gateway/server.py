from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import states
from .config import Settings, get_settings
from .db import Database, iso_now, require_active_lease
from .security import require_agent
from .storage import TaskStorage

PACKAGE_DIR = Path(__file__).resolve().parent
WEB_DIST_DIR = PACKAGE_DIR / "web" / "dist"
WEB_ASSETS_DIR = WEB_DIST_DIR / "assets"
WEB_ACTOR = "web"
ALLOWED_UPLOAD_EXTENSIONS = (
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".txt",
    ".rtf",
    ".csv",
)
ALLOWED_UPLOAD_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
        "text/plain",
        "text/rtf",
        "text/csv",
    }
)


class AgentRegisterRequest(BaseModel):
    name: str
    version: str = "unknown"
    hostname: str = "unknown"


class AgentHeartbeatRequest(BaseModel):
    status: str = "online"
    current_task_id: int | None = None
    disk_free_bytes: int | None = None
    tools: dict[str, str] = Field(default_factory=dict)


class PrinterSyncRequest(BaseModel):
    printers: list[dict[str, Any]]


class AgentEventRequest(BaseModel):
    lease_id: str
    event_seq: int
    event_type: str
    status: str | None = None
    message: str | None = None


class CupsJobRequest(BaseModel):
    lease_id: str
    cups_job_id: str
    status: str = states.SUBMITTED_TO_CUPS
    message: str | None = None


class ConfirmPrintRequest(BaseModel):
    printer_id: str
    copies: int = 1
    sides: str = "one-sided"
    media: str = "A4"
    orientation: str = "portrait"
    page_ranges: str = ""
    color_mode: str = "auto"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = Database(settings.database_path)
    storage = TaskStorage(settings.storage_root)
    app = FastAPI(title="Linux Print Gateway")
    app.state.settings = settings
    app.state.db = db
    app.state.storage = storage
    app.dependency_overrides[get_settings] = lambda: settings
    if WEB_ASSETS_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(WEB_ASSETS_DIR)), name="assets")

    @app.exception_handler(ValueError)
    def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        del request
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.on_event("startup")
    def startup() -> None:
        db.init()
        settings.storage_root.mkdir(parents=True, exist_ok=True)

    @app.get("/api/tasks")
    def api_list_tasks() -> dict[str, Any]:
        return {"tasks": [serialize_task(task) for task in db.list_tasks()]}

    @app.get("/api/service")
    def api_service_status() -> dict[str, Any]:
        return service_status(db)

    @app.post("/api/tasks/upload")
    async def api_upload_task(
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        if not has_available_printers(db):
            raise HTTPException(status_code=503, detail="service unavailable: no printer is available")
        if not is_allowed_upload(file.filename, file.content_type):
            raise HTTPException(status_code=400, detail="unsupported file type")
        content = await read_upload_within_limit(file, settings.max_upload_bytes)
        if not content:
            raise HTTPException(status_code=400, detail="empty upload")
        expires_at = (datetime.now(UTC) + timedelta(days=14)).isoformat()
        task_id = db.create_task(
            created_by=WEB_ACTOR,
            source_filename=file.filename or "upload.bin",
            source_mime=file.content_type or "application/octet-stream",
            expires_at=expires_at,
        )
        saved = storage.save_bytes(task_id=task_id, folder="original", filename="source.bin", content=content)
        db.set_task_file(
            task_id=task_id,
            kind="original",
            storage_path=saved.relative_path,
            filename=file.filename or "upload.bin",
            mime=file.content_type or "application/octet-stream",
            size_bytes=saved.size_bytes,
            sha256=saved.sha256,
        )
        db.mark_for_conversion(task_id, requires_confirmation=requires_preview_confirmation(file.filename, file.content_type))
        return {"task_id": task_id, "location": f"/tasks/{task_id}"}

    @app.get("/api/tasks/{task_id}")
    def api_task_detail(task_id: int) -> dict[str, Any]:
        task = require_task(db, task_id)
        return {
            "task": serialize_task(task),
            "events": [serialize_event(event) for event in db.list_task_events(task_id)],
            "printers": [serialize_printer(printer) for printer in list_printers(db)],
            "converted_pdf_available": db.get_task_file(task_id, "converted_pdf") is not None,
            "can_confirm": task["status"] in {states.PREVIEW_READY, states.WAITING_USER_CONFIRM},
        }

    @app.post("/api/tasks/{task_id}/confirm")
    def api_confirm_task(
        task_id: int,
        payload: ConfirmPrintRequest,
    ) -> dict[str, Any]:
        print_options = {
            "copies": max(1, min(payload.copies, 99)),
            "sides": payload.sides,
            "media": payload.media,
            "orientation": payload.orientation,
            "page_ranges": payload.page_ranges.strip(),
            "color_mode": payload.color_mode,
        }
        db.confirm_preview(task_id, printer_id=payload.printer_id, print_options=print_options, confirmed_by=WEB_ACTOR)
        return {"status": states.QUEUED_FOR_PRINT}

    @app.post("/api/tasks/{task_id}/cancel")
    def api_cancel_task(task_id: int) -> dict[str, Any]:
        task = require_task(db, task_id)
        if task["status"] in states.TERMINAL_STATES:
            raise HTTPException(status_code=400, detail="task is already terminal")
        db.update_status(task_id, states.CANCELLED, actor=WEB_ACTOR, message="cancelled from web")
        cleanup_if_needed(db, storage, task_id)
        return {"status": states.CANCELLED}

    @app.get("/api/tasks/{task_id}/files/{kind}")
    def download_task_file(task_id: int, kind: str) -> FileResponse:
        task_file = db.get_task_file(task_id, normalize_file_kind(kind))
        if task_file is None:
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(storage.resolve_relative(task_file["storage_path"]), filename=task_file["filename"])

    @app.post("/agent/register")
    def agent_register(payload: AgentRegisterRequest, agent_id: str = Depends(require_agent)) -> dict[str, str]:
        now = iso_now()
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO agents (id, name, token_label, version, hostname, status, created_at, updated_at)
                VALUES (?, ?, 'default', ?, ?, 'online', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    version = excluded.version,
                    hostname = excluded.hostname,
                    status = 'online',
                    updated_at = excluded.updated_at
                """,
                (agent_id, payload.name, payload.version, payload.hostname, now, now),
            )
        return {"status": "registered", "agent_id": agent_id}

    @app.post("/agent/heartbeat")
    def agent_heartbeat(payload: AgentHeartbeatRequest, agent_id: str = Depends(require_agent)) -> dict[str, str]:
        now = iso_now()
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE agents
                SET last_heartbeat_at = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, payload.status, now, agent_id),
            )
        return {"status": "ok"}

    @app.post("/agent/printers/sync")
    def sync_printers(payload: PrinterSyncRequest, agent_id: str = Depends(require_agent)) -> dict[str, int]:
        now = iso_now()
        with db.connect() as conn:
            agent = conn.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if agent is None:
                raise ValueError("agent is not registered")
            synced_printer_ids = []
            for printer in payload.printers:
                cups_name = str(printer["cups_name"])
                printer_id = f"{agent_id}:{cups_name}"
                synced_printer_ids.append(printer_id)
                conn.execute(
                    """
                    INSERT INTO printers (
                        id, agent_id, cups_name, display_name, capabilities_json,
                        capability_version, last_synced_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        display_name = excluded.display_name,
                        capabilities_json = excluded.capabilities_json,
                        capability_version = excluded.capability_version,
                        last_synced_at = excluded.last_synced_at
                    """,
                    (
                        printer_id,
                        agent_id,
                        cups_name,
                        str(printer.get("display_name") or cups_name),
                        json.dumps(printer.get("capabilities") or {}, ensure_ascii=False, sort_keys=True),
                        str(printer.get("capability_version") or now),
                        now,
                    ),
                )
            if synced_printer_ids:
                placeholders = ",".join("?" for _ in synced_printer_ids)
                conn.execute(
                    f"DELETE FROM printers WHERE agent_id = ? AND id NOT IN ({placeholders})",
                    (agent_id, *synced_printer_ids),
                )
            else:
                conn.execute("DELETE FROM printers WHERE agent_id = ?", (agent_id,))
        return {"synced": len(payload.printers)}

    @app.post("/agent/tasks/lease")
    def lease_task(agent_id: str = Depends(require_agent)) -> dict[str, Any]:
        task = db.lease_task(agent_id=agent_id, lease_seconds=settings.lease_seconds)
        if task is None:
            return {"task": None}
        action = "conversion" if task["status"] == states.CONVERTING else "print"
        return {"task": serialize_task(task), "action": action}

    @app.get("/agent/tasks/{task_id}/files/{kind}")
    def agent_download_file(task_id: int, kind: str, agent_id: str = Depends(require_agent)) -> FileResponse:
        del agent_id
        task_file = db.get_task_file(task_id, normalize_file_kind(kind))
        if task_file is None:
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(storage.resolve_relative(task_file["storage_path"]), filename=task_file["filename"])

    @app.put("/agent/tasks/{task_id}/files/converted-pdf")
    async def upload_converted_pdf(
        task_id: int,
        lease_id: str = Form(...),
        file: UploadFile = File(...),
        agent_id: str = Depends(require_agent),
    ) -> dict[str, str]:
        task = require_task(db, task_id)
        require_active_lease(task, agent_id=agent_id, lease_id=lease_id)
        content = await file.read()
        saved = storage.save_bytes(task_id=task_id, folder="converted", filename="document.pdf", content=content)
        db.set_task_file(
            task_id=task_id,
            kind="converted_pdf",
            storage_path=saved.relative_path,
            filename=file.filename or "document.pdf",
            mime=file.content_type or "application/pdf",
            size_bytes=saved.size_bytes,
            sha256=saved.sha256,
        )
        next_status = states.WAITING_USER_CONFIRM if task["requires_preview_confirmation"] else states.PREVIEW_READY
        db.update_status(task_id, next_status, actor=agent_id, message="converted PDF uploaded")
        return {"status": next_status, "sha256": saved.sha256}

    @app.put("/agent/tasks/{task_id}/files/previews/{page}")
    async def upload_preview(
        task_id: int,
        page: int,
        lease_id: str = Form(...),
        file: UploadFile = File(...),
        agent_id: str = Depends(require_agent),
    ) -> dict[str, str]:
        task = require_task(db, task_id)
        require_active_lease(task, agent_id=agent_id, lease_id=lease_id)
        content = await file.read()
        saved = storage.save_bytes(task_id=task_id, folder="previews", filename=f"page-{page:04d}.png", content=content)
        db.set_task_file(
            task_id=task_id,
            kind=f"preview_{page}",
            storage_path=saved.relative_path,
            filename=file.filename or f"page-{page:04d}.png",
            mime=file.content_type or "image/png",
            size_bytes=saved.size_bytes,
            sha256=saved.sha256,
        )
        return {"status": "stored", "sha256": saved.sha256}

    @app.post("/agent/tasks/{task_id}/events")
    def record_event(task_id: int, payload: AgentEventRequest, agent_id: str = Depends(require_agent)) -> dict[str, str]:
        db.record_agent_event(
            task_id=task_id,
            agent_id=agent_id,
            lease_id=payload.lease_id,
            event_seq=payload.event_seq,
            event_type=payload.event_type,
            status=payload.status,
            message=payload.message,
        )
        cleanup_if_needed(db, storage, task_id)
        return {"status": "recorded"}

    @app.post("/agent/tasks/{task_id}/cups-jobs")
    def record_cups_job(task_id: int, payload: CupsJobRequest, agent_id: str = Depends(require_agent)) -> dict[str, str]:
        db.record_cups_job(
            task_id=task_id,
            agent_id=agent_id,
            lease_id=payload.lease_id,
            cups_job_id=payload.cups_job_id,
            status=payload.status,
            message=payload.message,
        )
        cleanup_if_needed(db, storage, task_id)
        return {"status": "recorded"}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def react_root():
        return serve_react_app()

    @app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
    def react_fallback(full_path: str):
        if full_path.startswith(("api/", "agent/", "assets/")):
            raise HTTPException(status_code=404, detail="not found")
        return serve_react_app()

    return app


def require_task(db: Database, task_id: int) -> Any:
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


def list_printers(db: Database) -> list[Any]:
    with db.connect() as conn:
        return list(conn.execute("SELECT * FROM printers ORDER BY display_name ASC"))


def has_available_printers(db: Database) -> bool:
    with db.connect() as conn:
        row = conn.execute("SELECT 1 FROM printers LIMIT 1").fetchone()
        return row is not None


def normalize_file_kind(kind: str) -> str:
    if kind == "converted-pdf":
        return "converted_pdf"
    return kind


async def read_upload_within_limit(file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"file exceeds maximum size of {max_bytes} bytes",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def is_allowed_upload(filename: str | None, mime: str | None) -> bool:
    extension = Path(filename or "").suffix.lower()
    normalized_mime = (mime or "").split(";", 1)[0].strip().lower()
    return extension in ALLOWED_UPLOAD_EXTENSIONS or normalized_mime in ALLOWED_UPLOAD_MIME_TYPES


def service_status(db: Database) -> dict[str, Any]:
    printers = list_printers(db)
    return {
        "available": len(printers) > 0,
        "printer_count": len(printers),
        "allowed_uploads": {
            "extensions": list(ALLOWED_UPLOAD_EXTENSIONS),
            "mime_types": sorted(ALLOWED_UPLOAD_MIME_TYPES),
        },
    }


def requires_preview_confirmation(filename: str | None, mime: str | None) -> bool:
    name = (filename or "").lower()
    mime = (mime or "").lower()
    if mime == "application/pdf" or name.endswith(".pdf"):
        return False
    if mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff")):
        return False
    return True


def serialize_task(task: Any) -> dict[str, Any]:
    options = json.loads(task["print_options_json"]) if task["print_options_json"] else None
    return {
        "id": task["id"],
        "created_by": task["created_by"],
        "status": task["status"],
        "source_filename": task["source_filename"],
        "source_mime": task["source_mime"],
        "requires_preview_confirmation": bool(task["requires_preview_confirmation"]),
        "preview_confirmed_at": task["preview_confirmed_at"],
        "printer_id": task["printer_id"],
        "print_options": options,
        "target_pdf_sha256": task["target_pdf_sha256"],
        "lease_id": task["lease_id"],
        "lease_expires_at": task["lease_expires_at"],
        "cups_job_id": task["cups_job_id"],
        "last_error_code": task["last_error_code"],
        "last_error_message": task["last_error_message"],
        "files_deleted_at": task["files_deleted_at"],
        "files_delete_reason": task["files_delete_reason"],
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
        "expires_at": task["expires_at"],
    }


def serialize_event(event: Any) -> dict[str, Any]:
    return {
        "id": event["id"],
        "event_type": event["event_type"],
        "status": event["status"],
        "actor": event["actor"],
        "message": event["message"],
        "lease_id": event["lease_id"],
        "agent_id": event["agent_id"],
        "event_seq": event["event_seq"],
        "created_at": event["created_at"],
    }


def serialize_printer(printer: Any) -> dict[str, Any]:
    return {
        "id": printer["id"],
        "agent_id": printer["agent_id"],
        "cups_name": printer["cups_name"],
        "display_name": printer["display_name"],
        "capabilities": json.loads(printer["capabilities_json"] or "{}"),
        "capability_version": printer["capability_version"],
        "last_synced_at": printer["last_synced_at"],
    }


def serve_react_app():
    index_file = WEB_DIST_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse(
            "<h1>React build missing</h1><p>Run <code>npm install</code> and <code>npm run build</code>.</p>",
            status_code=503,
        )
    return FileResponse(index_file, media_type="text/html")


def cleanup_if_needed(db: Database, storage: TaskStorage, task_id: int) -> None:
    task = db.get_task(task_id)
    if task is None or task["files_deleted_at"]:
        return
    if states.requires_success_cleanup(task["status"]):
        storage.cleanup_task_files(task_id)
        db.mark_files_deleted(task_id, reason=f"status:{task['status']}")
    if task["status"] in {states.CANCELLED, states.EXPIRED}:
        storage.cleanup_task_files(task_id)
        db.mark_files_deleted(task_id, reason=f"status:{task['status']}")


def main() -> None:
    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
