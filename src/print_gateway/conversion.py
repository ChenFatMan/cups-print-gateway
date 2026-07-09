from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ConversionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str


class Converter:
    def __init__(self, timeout_seconds: int = 120) -> None:
        self.timeout_seconds = timeout_seconds

    def convert_to_pdf(self, source: Path, *, filename: str, mime: str, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        lower_name = filename.lower()
        lower_mime = mime.lower()
        if lower_mime == "application/pdf" or lower_name.endswith(".pdf"):
            target = output_dir / "document.pdf"
            shutil.copyfile(source, target)
            return target
        if lower_mime.startswith("image/") or lower_name.endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff")):
            return self._convert_image_to_pdf(source, output_dir)
        return self._convert_with_libreoffice(source, output_dir)

    def render_preview(self, pdf_path: Path, output_dir: Path) -> Path | None:
        pdftoppm = shutil.which("pdftoppm")
        if not pdftoppm:
            return None
        output_dir.mkdir(parents=True, exist_ok=True)
        prefix = output_dir / "page"
        self._run([pdftoppm, "-png", "-singlefile", "-f", "1", "-l", "1", str(pdf_path), str(prefix)])
        preview = output_dir / "page.png"
        return preview if preview.exists() else None

    def _convert_image_to_pdf(self, source: Path, output_dir: Path) -> Path:
        target = output_dir / "document.pdf"
        magick = shutil.which("magick")
        if magick:
            self._run([magick, str(source), str(target)])
            return target
        convert = shutil.which("convert")
        if convert:
            self._run([convert, str(source), str(target)])
            return target
        raise ConversionError("ImageMagick is required to convert images to PDF")

    def _convert_with_libreoffice(self, source: Path, output_dir: Path) -> Path:
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            raise ConversionError("LibreOffice headless is required to convert this file")
        profile_dir = output_dir / "lo-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        self._run(
            [
                soffice,
                "--headless",
                f"-env:UserInstallation=file://{profile_dir.resolve()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir),
                str(source),
            ]
        )
        pdfs = sorted(output_dir.glob("*.pdf"))
        if not pdfs:
            raise ConversionError("conversion did not produce a PDF")
        target = output_dir / "document.pdf"
        if pdfs[0] != target:
            pdfs[0].replace(target)
        return target

    def _run(self, args: list[str]) -> CommandResult:
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise ConversionError(f"command not found: {args[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(f"command timed out: {args[0]}") from exc
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip()
            raise ConversionError(f"conversion command failed: {stderr}")
        return CommandResult(stdout=completed.stdout, stderr=completed.stderr)
