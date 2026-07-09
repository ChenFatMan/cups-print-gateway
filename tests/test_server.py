from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from print_gateway import states
from print_gateway.config import Settings
from print_gateway.db import Database
from print_gateway.server import create_app


def make_settings(tmp_path) -> Settings:
    return Settings(
        database_path=tmp_path / "gateway.sqlite3",
        storage_root=tmp_path / "storage",
        agent_token="agent-token",
        host="127.0.0.1",
        port=8000,
        lease_seconds=300,
        cleanup_success_after_seconds=300,
        retain_failed_seconds=86400,
        max_upload_bytes=20 * 1024 * 1024,
    )


def agent_headers(agent_id: str = "agent-1") -> dict[str, str]:
    return {"X-Agent-Id": agent_id, "X-Agent-Token": "agent-token"}


def upload_file(
    client: TestClient,
    *,
    name: str = "../../invoice.pdf",
    content: bytes = b"%PDF-1.4\n%",
    mime: str = "application/pdf",
) -> int:
    response = client.post(
        "/api/tasks/upload",
        files={"file": (name, content, mime)},
    )
    assert response.status_code == 200
    return int(response.json()["task_id"])


def upload_pdf(client: TestClient, name: str = "../../invoice.pdf") -> int:
    return upload_file(client, name=name)


def sync_printer(client: TestClient) -> None:
    response = client.post(
        "/agent/register",
        headers=agent_headers(),
        json={"name": "agent-1", "version": "test", "hostname": "test-host"},
    )
    assert response.status_code == 200
    response = client.post(
        "/agent/printers/sync",
        headers=agent_headers(),
        json={"printers": [{"cups_name": "office", "display_name": "Office Printer", "capabilities": {}}]},
    )
    assert response.status_code == 200


def lease(client: TestClient) -> dict:
    response = client.post("/agent/tasks/lease", headers=agent_headers(), json={})
    assert response.status_code == 200
    task = response.json()["task"]
    assert task is not None
    return task


def test_upload_uses_isolated_storage_path(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        sync_printer(client)
        task_id = upload_pdf(client)

    db = Database(settings.database_path)
    task_file = db.get_task_file(task_id, "original")
    assert task_file is not None
    assert task_file["storage_path"] == f"tasks/{task_id}/original/source.bin"
    assert "../" not in task_file["storage_path"]


def test_web_api_is_public(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/tasks")
        assert response.status_code == 200
        assert response.json() == {"tasks": []}


def test_agent_api_still_requires_valid_token(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/agent/register",
            headers={"X-Agent-Id": "agent-1", "X-Agent-Token": "wrong-token"},
            json={"name": "agent-1", "version": "test", "hostname": "test-host"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "invalid agent token"


def test_upload_rejects_when_no_printer_is_available(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/tasks/upload",
            files={"file": ("invoice.pdf", b"%PDF-1.4\n%", "application/pdf")},
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "service unavailable: no printer is available"


def test_upload_rejects_unsupported_file_type(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        sync_printer(client)
        response = client.post(
            "/api/tasks/upload",
            files={"file": ("installer.exe", b"MZ", "application/octet-stream")},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "unsupported file type"


def test_task_list_hides_terminal_history(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        sync_printer(client)
        active_task_id = upload_pdf(client, "active.pdf")
        historical_task_id = upload_pdf(client, "historical.pdf")

        db = Database(settings.database_path)
        db.update_status(historical_task_id, states.PRINTED, actor="test")

        response = client.get("/api/tasks")
        assert response.status_code == 200
        task_ids = [task["id"] for task in response.json()["tasks"]]
        assert task_ids == [active_task_id]


def test_service_status_reports_printer_availability_and_allowed_uploads(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/service")
        assert response.status_code == 200
        payload = response.json()
        assert payload["available"] is False
        assert payload["printer_count"] == 0
        assert ".pdf" in payload["allowed_uploads"]["extensions"]

        sync_printer(client)
        response = client.get("/api/service")
        assert response.status_code == 200
        payload = response.json()
        assert payload["available"] is True
        assert payload["printer_count"] == 1


def test_empty_printer_sync_makes_service_unavailable(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        sync_printer(client)
        response = client.post(
            "/agent/printers/sync",
            headers=agent_headers(),
            json={"printers": []},
        )
        assert response.status_code == 200
        assert response.json() == {"synced": 0}

        response = client.get("/api/service")
        assert response.status_code == 200
        assert response.json()["available"] is False
        assert response.json()["printer_count"] == 0


def test_printed_task_cleans_files_but_keeps_metadata(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        sync_printer(client)
        task_id = upload_pdf(client, "invoice.pdf")

        conversion_task = lease(client)
        assert conversion_task["status"] == states.CONVERTING
        lease_id = conversion_task["lease_id"]
        response = client.put(
            f"/agent/tasks/{task_id}/files/converted-pdf",
            headers=agent_headers(),
            data={"lease_id": lease_id},
            files={"file": ("document.pdf", b"%PDF-1.4\nconverted", "application/pdf")},
        )
        assert response.status_code == 200

        response = client.post(
            f"/api/tasks/{task_id}/confirm",
            json={"printer_id": "agent-1:office", "copies": 1, "sides": "one-sided", "media": "A4"},
        )
        assert response.status_code == 200

        print_task = lease(client)
        assert print_task["status"] == states.PRINTING
        print_lease = print_task["lease_id"]
        response = client.post(
            f"/agent/tasks/{task_id}/cups-jobs",
            headers=agent_headers(),
            json={
                "lease_id": print_lease,
                "cups_job_id": "office-1",
                "status": states.SUBMITTED_TO_CUPS,
                "message": "request id is office-1",
            },
        )
        assert response.status_code == 200
        response = client.post(
            f"/agent/tasks/{task_id}/events",
            headers=agent_headers(),
            json={
                "lease_id": print_lease,
                "event_seq": 2,
                "event_type": "print_finished",
                "status": states.PRINTED,
                "message": "CUPS job completed",
            },
        )
        assert response.status_code == 200

    db = Database(settings.database_path)
    task = db.get_task(task_id)
    assert task is not None
    assert task["status"] == states.PRINTED
    assert task["files_deleted_at"] is not None
    assert task["source_filename"] == "invoice.pdf"
    assert not (settings.storage_root / "tasks" / str(task_id)).exists()


def test_expired_lease_rejects_agent_event(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        sync_printer(client)
        task_id = upload_pdf(client)
        task = lease(client)
        lease_id = task["lease_id"]

        db = Database(settings.database_path)
        with db.connect() as conn:
            conn.execute(
                "UPDATE print_tasks SET lease_expires_at = ? WHERE id = ?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), task_id),
            )

        response = client.post(
            f"/agent/tasks/{task_id}/events",
            headers=agent_headers(),
            json={
                "lease_id": lease_id,
                "event_seq": 1,
                "event_type": "conversion_finished",
                "status": states.PREVIEW_READY,
                "message": "late event",
            },
        )
        assert response.status_code == 400
        assert "expired" in response.json()["detail"]


def test_rejects_second_cups_job_id_for_same_task(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        sync_printer(client)
        task_id = upload_pdf(client, "invoice.pdf")
        conversion_task = lease(client)
        response = client.put(
            f"/agent/tasks/{task_id}/files/converted-pdf",
            headers=agent_headers(),
            data={"lease_id": conversion_task["lease_id"]},
            files={"file": ("document.pdf", b"%PDF-1.4\nconverted", "application/pdf")},
        )
        assert response.status_code == 200
        response = client.post(
            f"/api/tasks/{task_id}/confirm",
            json={"printer_id": "agent-1:office"},
        )
        assert response.status_code == 200
        print_task = lease(client)
        print_lease = print_task["lease_id"]

        response = client.post(
            f"/agent/tasks/{task_id}/cups-jobs",
            headers=agent_headers(),
            json={"lease_id": print_lease, "cups_job_id": "office-1", "status": states.SUBMITTED_TO_CUPS},
        )
        assert response.status_code == 200
        response = client.post(
            f"/agent/tasks/{task_id}/cups-jobs",
            headers=agent_headers(),
            json={"lease_id": print_lease, "cups_job_id": "office-2", "status": states.SUBMITTED_TO_CUPS},
        )
        assert response.status_code == 400
        assert "different CUPS job id" in response.json()["detail"]


def test_confirm_rejects_unknown_printer(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        sync_printer(client)
        task_id = upload_pdf(client, "invoice.pdf")
        conversion_task = lease(client)
        response = client.put(
            f"/agent/tasks/{task_id}/files/converted-pdf",
            headers=agent_headers(),
            data={"lease_id": conversion_task["lease_id"]},
            files={"file": ("document.pdf", b"%PDF-1.4\nconverted", "application/pdf")},
        )
        assert response.status_code == 200

        response = client.post(
            f"/api/tasks/{task_id}/confirm",
            json={"printer_id": "agent-1:nonexistent"},
        )
        assert response.status_code == 400
        assert "printer is not available" in response.json()["detail"]

    db = Database(settings.database_path)
    task = db.get_task(task_id)
    assert task is not None
    assert task["status"] != states.QUEUED_FOR_PRINT


def test_upload_rejects_file_exceeding_max_size(tmp_path) -> None:
    settings = make_settings(tmp_path)
    object.__setattr__(settings, "max_upload_bytes", 1024)
    app = create_app(settings)
    with TestClient(app) as client:
        sync_printer(client)
        oversized = b"%PDF-1.4\n" + b"0" * 2048
        response = client.post(
            "/api/tasks/upload",
            files={"file": ("big.pdf", oversized, "application/pdf")},
        )
        assert response.status_code == 413
        assert "maximum size" in response.json()["detail"]
