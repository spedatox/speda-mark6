"""Local OCR with a whole-document deadline and disposable, bounded image files."""
import re
import shutil
import struct
import subprocess
import tempfile
import time
from pathlib import Path

from app.services.library_extract import ExtractionError


class LocalOCR:
    def __init__(self, *, languages="tur+eng", timeout=300):
        if not re.fullmatch(r"[a-z]{3}(?:\+[a-z]{3}){0,4}", languages):
            raise ValueError("Invalid OCR language list")
        self.languages = languages
        self.deadline = time.monotonic() + timeout
        self.tesseract = shutil.which("tesseract")
        self.pdftoppm = shutil.which("pdftoppm")

    def _run(self, command):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ExtractionError("OCR exceeded the document time limit")
        try:
            subprocess.run(command, check=True, timeout=remaining, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExtractionError("Local OCR failed or timed out; original retained") from exc

    def _recognize(self, image, directory):
        if not self.tesseract:
            raise ExtractionError("Local OCR runtime is unavailable")
        output = directory / "transcription"
        self._run([self.tesseract, str(image), str(output), "-l", self.languages, "--psm", "3"])
        result = output.with_suffix(".txt")
        if not result.exists() or result.stat().st_size > 8_000_000:
            raise ExtractionError("OCR output is missing or exceeds the size limit")
        try:
            value = result.read_text(encoding="utf-8").strip()
        except UnicodeError as exc:
            raise ExtractionError("OCR did not return UTF-8 text") from exc
        if not value or "\x00" in value:
            raise ExtractionError("OCR found no usable text")
        return value

    def pdf_page(self, blob, number):
        if not self.pdftoppm or not self.tesseract:
            raise ExtractionError("Local PDF OCR requires Poppler and Tesseract")
        with tempfile.TemporaryDirectory(prefix="iacon-ocr-") as scratch:
            directory = Path(scratch)
            original = directory / "original.pdf"
            original.write_bytes(blob)
            prefix = directory / "page"
            self._run([self.pdftoppm, "-f", str(number), "-l", str(number), "-singlefile",
                       "-scale-to", "2400", "-png", str(original), str(prefix)])
            image = prefix.with_suffix(".png")
            if not image.exists() or image.stat().st_size > 25 * 1024**2:
                raise ExtractionError("Rendered OCR page exceeds the size limit")
            return self._recognize(image, directory)

    def image(self, blob, mime_type):
        width, height = image_dimensions(blob, mime_type)
        if not width or not height or width * height > 12_000_000 or max(width, height) > 12000:
            raise ExtractionError("OCR image exceeds 12 million pixels or dimension limits")
        with tempfile.TemporaryDirectory(prefix="iacon-ocr-") as scratch:
            directory = Path(scratch)
            image = directory / ("original.png" if mime_type == "image/png" else "original.jpg")
            image.write_bytes(blob)
            return self._recognize(image, directory)


def image_dimensions(blob, mime_type):
    if mime_type == "image/png" and blob[:8] == b"\x89PNG\r\n\x1a\n" and blob[12:16] == b"IHDR" and len(blob) >= 24:
        return struct.unpack(">II", blob[16:24])
    if mime_type == "image/jpeg" and blob[:2] == b"\xff\xd8":
        position = 2
        while position < len(blob):
            if blob[position] != 255:
                break
            while position < len(blob) and blob[position] == 255:
                position += 1
            if position >= len(blob):
                break
            marker = blob[position]
            position += 1
            if marker in {0xD9, 0xDA}:
                break
            if marker in {0x01, *range(0xD0, 0xD9)}:
                continue
            if position + 2 > len(blob):
                break
            length = int.from_bytes(blob[position:position + 2], "big")
            if length < 2 or position + length > len(blob):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and length >= 7:
                height, width = struct.unpack(">HH", blob[position + 3:position + 7])
                return width, height
            position += length
    raise ExtractionError("Image dimensions could not be verified")
