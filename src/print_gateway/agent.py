from __future__ import annotations

import argparse
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

from . import states
from .conversion import ConversionError, Converter
from .cups import CupsClient, CupsError


class AgentClient:
    def __init__(self, *, server: str, agent_id: str, token: str, timeout: int = 30) -> None:
        self.server = server.rstrip("/")
        self.agent_id = agent_id
        self.session = requests.Session()
        self.session.headers.update({"X-Agent-Id": agent_id, "X-Agent-Token": token})
        self.timeout = timeout

    def register(self) -> None:
        self._post("/agent/register", json={"name": self.agent_id, "version": "0.1.0", "hostname": os.uname().nodename})

    def heartbeat(self) -> None:
        self._post("/agent/heartbeat", json={"status": "online"})

    def sync_printers(self, printers: list[dict[str, Any]]) -> None:
        self._post("/agent/printers/sync", json={"printers": printers})

    def lease_task(self) -> dict[str, Any] | None:
        payload = self._post("/agent/tasks/lease", json={})
        return payload.get("task")

    def download_file(self, task_id: int, kind: str, target: Path) -> None:
        response = self.session.get(f"{self.server}/agent/tasks/{task_id}/files/{kind}", timeout=self.timeout)
        response.raise_for_status()
        target.write_bytes(response.content)

    def upload_converted_pdf(self, task_id: int, lease_id: str, pdf_path: Path) -> None:
        with pdf_path.open("rb") as handle:
            response = self.session.put(
                f"{self.server}/agent/tasks/{task_id}/files/converted-pdf",
                data={"lease_id": lease_id},
                files={"file": ("document.pdf", handle, "application/pdf")},
                timeout=self.timeout,
            )
        response.raise_for_status()

    def upload_preview(self, task_id: int, lease_id: str, page: int, preview_path: Path) -> None:
        with preview_path.open("rb") as handle:
            response = self.session.put(
                f"{self.server}/agent/tasks/{task_id}/files/previews/{page}",
                data={"lease_id": lease_id},
                files={"file": (preview_path.name, handle, "image/png")},
                timeout=self.timeout,
            )
        response.raise_for_status()

    def event(self, task_id: int, lease_id: str, event_seq: int, event_type: str, status: str | None, message: str | None) -> None:
        self._post(
            f"/agent/tasks/{task_id}/events",
            json={
                "lease_id": lease_id,
                "event_seq": event_seq,
                "event_type": event_type,
                "status": status,
                "message": message,
            },
        )

    def cups_job(self, task_id: int, lease_id: str, cups_job_id: str, status: str, message: str | None) -> None:
        self._post(
            f"/agent/tasks/{task_id}/cups-jobs",
            json={"lease_id": lease_id, "cups_job_id": cups_job_id, "status": status, "message": message},
        )

    def _post(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(f"{self.server}{path}", json=json, timeout=self.timeout)
        response.raise_for_status()
        return response.json()


class PrintAgent:
    def __init__(self, client: AgentClient, converter: Converter | None = None, cups: CupsClient | None = None) -> None:
        self.client = client
        self.converter = converter or Converter()
        self.cups = cups or CupsClient()

    def run_once(self) -> bool:
        self.client.heartbeat()
        task = self.client.lease_task()
        if not task:
            return False
        if task["status"] == states.CONVERTING:
            self._process_conversion(task)
            return True
        if task["status"] == states.PRINTING:
            self._process_print(task)
            return True
        return False

    def sync_printers(self) -> None:
        printers = []
        for printer in self.cups.list_printers():
            printers.append(
                {
                    "cups_name": printer.cups_name,
                    "display_name": printer.display_name,
                    "capabilities": printer.capabilities,
                    "capability_version": "cups",
                }
            )
        self.client.sync_printers(printers)

    def _process_conversion(self, task: dict[str, Any]) -> None:
        task_id = int(task["id"])
        lease_id = str(task["lease_id"])
        with tempfile.TemporaryDirectory(prefix=f"print-gateway-{task_id}-") as temp:
            temp_dir = Path(temp)
            source = temp_dir / "source.bin"
            output_dir = temp_dir / "output"
            preview_dir = temp_dir / "preview"
            try:
                self.client.download_file(task_id, "original", source)
                pdf = self.converter.convert_to_pdf(
                    source,
                    filename=str(task["source_filename"]),
                    mime=str(task["source_mime"]),
                    output_dir=output_dir,
                )
                self.client.upload_converted_pdf(task_id, lease_id, pdf)
                preview = self.converter.render_preview(pdf, preview_dir)
                if preview:
                    self.client.upload_preview(task_id, lease_id, 1, preview)
                self.client.event(task_id, lease_id, 1, "conversion_finished", None, "conversion finished")
            except (ConversionError, requests.RequestException, OSError) as exc:
                self.client.event(task_id, lease_id, 1, "conversion_failed", states.CONVERSION_FAILED, str(exc))

    def _process_print(self, task: dict[str, Any]) -> None:
        task_id = int(task["id"])
        lease_id = str(task["lease_id"])
        printer_id = str(task["printer_id"])
        printer_name = printer_id.split(":", 1)[-1]
        options = task.get("print_options") or {}
        with tempfile.TemporaryDirectory(prefix=f"print-gateway-print-{task_id}-") as temp:
            pdf = Path(temp) / "document.pdf"
            try:
                self.client.download_file(task_id, "converted-pdf", pdf)
                submitted = self.cups.submit_pdf(printer_name=printer_name, pdf_path=pdf, options=options)
                self.client.cups_job(task_id, lease_id, submitted.cups_job_id, states.SUBMITTED_TO_CUPS, submitted.message)
                result = self.cups.wait_for_completion(submitted.cups_job_id)
                if result == "printed":
                    self.client.event(task_id, lease_id, 2, "print_finished", states.PRINTED, "CUPS job completed")
                else:
                    self.client.event(
                        task_id,
                        lease_id,
                        2,
                        "print_status_unknown",
                        states.PRINT_STATUS_UNKNOWN,
                        "CUPS accepted the job but final completion could not be confirmed",
                    )
            except (CupsError, requests.RequestException, OSError) as exc:
                self.client.event(task_id, lease_id, 2, "print_failed", states.PRINT_FAILED, str(exc))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Linux print gateway agent")
    parser.add_argument("--server", default=os.environ.get("PRINT_GATEWAY_SERVER", "http://127.0.0.1:8000"))
    parser.add_argument("--agent-id", default=os.environ.get("PRINT_GATEWAY_AGENT_ID", "linux-workstation"))
    parser.add_argument("--token", default=os.environ.get("PRINT_GATEWAY_AGENT_TOKEN", "dev-agent-token"))
    parser.add_argument("--once", action="store_true", help="process at most one task and exit")
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--skip-printer-sync", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    client = AgentClient(server=args.server, agent_id=args.agent_id, token=args.token)
    agent = PrintAgent(client)
    client.register()
    if not args.skip_printer_sync:
        try:
            agent.sync_printers()
        except CupsError as exc:
            print(f"printer sync failed: {exc}")
    while True:
        processed = agent.run_once()
        if args.once:
            break
        if not processed:
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
