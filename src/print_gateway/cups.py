from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ORIENTATION_REQUESTED = {"portrait": 3, "landscape": 4}


class CupsError(RuntimeError):
    pass


@dataclass(frozen=True)
class Printer:
    cups_name: str
    display_name: str
    capabilities: dict[str, Any]


@dataclass(frozen=True)
class SubmittedJob:
    cups_job_id: str
    message: str


class CupsClient:
    def __init__(self, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds

    def list_printers(self) -> list[Printer]:
        lpstat = self._require("lpstat")
        output = self._run([lpstat, "-p"]).stdout
        printers: list[Printer] = []
        for line in output.splitlines():
            match = re.match(r"printer\s+(\S+)\s+", line)
            if not match:
                continue
            name = match.group(1)
            printers.append(Printer(cups_name=name, display_name=name, capabilities=self.get_capabilities(name)))
        return printers

    def get_capabilities(self, printer_name: str) -> dict[str, Any]:
        lpoptions = self._require("lpoptions")
        result = self._run([lpoptions, "-p", printer_name, "-l"], allow_failure=True)
        capabilities: dict[str, Any] = {}
        for line in result.stdout.splitlines():
            if ":" not in line:
                continue
            key, values = line.split(":", 1)
            capabilities[key.strip()] = values.strip()
        return capabilities

    def submit_pdf(self, *, printer_name: str, pdf_path: Path, options: dict[str, Any]) -> SubmittedJob:
        lp = self._require("lp")
        args = [lp, "-d", printer_name]
        copies = int(options.get("copies") or 1)
        args.extend(["-n", str(max(1, min(copies, 99)))])
        for cups_option in self._cups_options(options):
            args.extend(["-o", cups_option])
        args.append(str(pdf_path))
        result = self._run(args)
        job_id = parse_job_id(result.stdout)
        if not job_id:
            raise CupsError(f"CUPS did not return a job id: {result.stdout.strip()}")
        return SubmittedJob(cups_job_id=job_id, message=result.stdout.strip())

    def wait_for_completion(self, cups_job_id: str, *, timeout_seconds: int = 60) -> str:
        lpstat = self._require("lpstat")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            not_completed = self._run([lpstat, "-W", "not-completed", "-o", cups_job_id], allow_failure=True)
            if cups_job_id not in not_completed.stdout:
                completed = self._run([lpstat, "-W", "completed", "-o", cups_job_id], allow_failure=True)
                if cups_job_id in completed.stdout:
                    return "printed"
                return "unknown"
            time.sleep(2)
        return "unknown"

    def _cups_options(self, options: dict[str, Any]) -> list[str]:
        result: list[str] = []
        sides = options.get("sides")
        if sides and sides != "default":
            result.append(f"sides={sides}")
        media = options.get("media")
        if media:
            result.append(f"media={media}")
        page_ranges = options.get("page_ranges")
        if page_ranges:
            result.append(f"page-ranges={page_ranges}")
        color_mode = options.get("color_mode")
        if color_mode and color_mode != "auto":
            result.append(f"print-color-mode={color_mode}")
        orientation = ORIENTATION_REQUESTED.get(str(options.get("orientation") or "").lower())
        if orientation is not None:
            result.append(f"orientation-requested={orientation}")
        if options.get("fit_to_page"):
            result.append("fit-to-page")
        return result

    def _require(self, command: str) -> str:
        path = shutil.which(command)
        if not path:
            raise CupsError(f"required CUPS command not found: {command}")
        return path

    def _run(self, args: list[str], *, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise CupsError(f"command not found: {args[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CupsError(f"command timed out: {args[0]}") from exc
        if completed.returncode != 0 and not allow_failure:
            stderr = completed.stderr.strip() or completed.stdout.strip()
            raise CupsError(f"CUPS command failed: {stderr}")
        return completed


def parse_job_id(output: str) -> str | None:
    match = re.search(r"request id is\s+(\S+-\d+)", output)
    if match:
        return match.group(1)
    match = re.search(r"\b(\S+-\d+)\b", output)
    return match.group(1) if match else None
