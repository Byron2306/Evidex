from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IngestedDoc:
    path: Path
    text: str


def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return "\n".join(parts)
    except Exception:
        return ""


def _read_docx(path: Path) -> str:
    try:
        from docx import Document

        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception:
        return ""


def _read_csv(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
            reader = csv.reader(f)
            rows = [", ".join(r) for r in reader]
        return "\n".join(rows)
    except Exception:
        return ""


def _read_xlsx(path: Path) -> str:
    try:
        import pandas as pd

        xls = pd.ExcelFile(path)
        parts: list[str] = []
        for sheet in xls.sheet_names[:5]:
            df = xls.parse(sheet)
            parts.append(f"[Sheet: {sheet}]")
            parts.append(df.head(50).to_csv(index=False))
        return "\n".join(parts)
    except Exception:
        return ""


def ingest_uploads(upload_dir: Path) -> list[IngestedDoc]:
    docs: list[IngestedDoc] = []
    if not upload_dir.exists():
        return docs

    for path in sorted(upload_dir.rglob("*")):
        if path.is_dir():
            continue
        suffix = path.suffix.lower()
        if suffix in {".txt", ".md"}:
            text = _read_text_file(path)
        elif suffix == ".pdf":
            text = _read_pdf(path)
        elif suffix == ".docx":
            text = _read_docx(path)
        elif suffix == ".csv":
            text = _read_csv(path)
        elif suffix in {".xlsx", ".xls"}:
            text = _read_xlsx(path)
        else:
            text = ""
        docs.append(IngestedDoc(path=path, text=text))

    return docs
