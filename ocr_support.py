"""Resolve a local Tesseract executable without inventing OCR results."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def resolve_tesseract_executable(configured: str | None = None) -> str:
    if configured:
        expanded = Path(os.path.expandvars(configured)).expanduser()
        if expanded.is_file():
            return str(expanded.resolve())
        discovered = shutil.which(configured)
        if discovered:
            return discovered
        raise ValueError(
            "ocr_engine_not_found; set tesseract_executable to the full "
            "path of tesseract.exe or add Tesseract to PATH")

    for command in ("tesseract.exe", "tesseract"):
        discovered = shutil.which(command)
        if discovered:
            return discovered
    roots = (os.environ.get("ProgramFiles"),
             os.environ.get("ProgramFiles(x86)"),
             os.environ.get("LOCALAPPDATA"))
    relative = ("Tesseract-OCR/tesseract.exe",
                "Programs/Tesseract-OCR/tesseract.exe")
    candidates = [Path(root) / item for root in roots if root
                  for item in relative]
    candidates.extend(Path(root) / "Microsoft/WinGet/Packages" /
                      "UB-Mannheim.TesseractOCR" / "tesseract.exe"
                      for root in roots if root)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    raise ValueError(
        "ocr_engine_not_found; install Tesseract OCR or set "
        "tesseract_executable to the full path of tesseract.exe")