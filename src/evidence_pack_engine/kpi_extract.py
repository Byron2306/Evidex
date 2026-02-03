from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExtractedKpiSheet:
    path: Path
    sheet: str | None
    row_count: int


def _norm(s: str) -> str:
    return " ".join(str(s or "").strip().lower().replace("_", " ").split())


def _choose_column(columns: list[str], synonyms: list[str]) -> str | None:
    norm_cols = {_norm(c): c for c in columns}
    # exact match on normalized strings
    for syn in synonyms:
        key = _norm(syn)
        if key in norm_cols:
            return norm_cols[key]
    # substring contains (handles long descriptive headers)
    for c in columns:
        nc = _norm(c)
        for syn in synonyms:
            if _norm(syn) in nc:
                return c
    return None


def _read_table(path: Path):
    import pandas as pd

    suf = path.suffix.lower()
    if suf == ".csv":
        return [(None, pd.read_csv(path))]
    if suf in {".xlsx", ".xls"}:
        xls = pd.ExcelFile(path)
        out = []
        for sheet in xls.sheet_names:
            try:
                out.append((sheet, pd.read_excel(xls, sheet_name=sheet)))
            except Exception:
                continue
        return out
    return []


def find_kpi_sheets(uploads_dir: Path) -> list[ExtractedKpiSheet]:
    """Best-effort discovery of KPI sheets in uploads/.

    We look for CSV/XLSX files that contain a plausible KPI/indicator column.
    """

    candidates: list[ExtractedKpiSheet] = []
    if not uploads_dir.exists():
        return candidates

    for p in uploads_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in {".csv", ".xlsx", ".xls"}:
            continue

        for sheet_name, df in _read_table(p):
            try:
                cols = [str(c) for c in df.columns]
            except Exception:
                continue

            name_col = _choose_column(
                cols,
                [
                    "kpi",
                    "indicator",
                    "metric",
                    "measure",
                    "name",
                    "kpa",
                    "result area",
                ],
            )
            if not name_col:
                continue

            row_count = int(getattr(df, "shape", [0])[0] or 0)
            if row_count <= 0:
                continue
            candidates.append(ExtractedKpiSheet(path=p, sheet=sheet_name, row_count=row_count))

    # Prefer files with "kpi"/"indicator" in name, then larger tables.
    def score(c: ExtractedKpiSheet) -> tuple[int, int]:
        n = _norm(c.path.name)
        name_score = 2 if ("kpi" in n or "indicator" in n) else 0
        return (name_score, c.row_count)

    return sorted(candidates, key=score, reverse=True)


def extract_kpis_from_uploads(uploads_dir: Path, *, max_kpis: int = 200):
    """Extract KPI rows from the best candidate KPI sheet.

    Returns: (kpis, provenance)
      - kpis: list[dict] with keys name/target/actual/measurement
      - provenance: info about which sheet was used
    """

    import pandas as pd

    from .config import KPI

    sheets = find_kpi_sheets(uploads_dir)
    if not sheets:
        return [], None

    chosen = sheets[0]
    tables = _read_table(chosen.path)
    df = None
    for sheet_name, t in tables:
        if sheet_name == chosen.sheet:
            df = t
            break
    if df is None and tables:
        df = tables[0][1]
    if df is None:
        return [], None

    df = df.copy()
    df.columns = [str(c) for c in df.columns]

    name_col = _choose_column(
        list(df.columns),
        ["kpi", "indicator", "metric", "measure", "name", "kpa", "result area"],
    )
    if not name_col:
        return [], None

    target_col = _choose_column(list(df.columns), ["target", "planned", "plan", "baseline/target", "expected"]) 
    actual_col = _choose_column(list(df.columns), ["actual", "achieved", "result", "value", "current"]) 
    measurement_col = _choose_column(
        list(df.columns),
        ["measurement", "how measured", "method", "definition", "means of verification", "mov"],
    )

    kpis: list[KPI] = []
    for _, row in df.iterrows():
        name = str(row.get(name_col, "") if isinstance(row, pd.Series) else "").strip()
        if not name or name.lower() in {"nan", "none"}:
            continue
        if len(kpis) >= max_kpis:
            break

        target = "" if not target_col else row.get(target_col, "")
        actual = "" if not actual_col else row.get(actual_col, "")
        measurement = "" if not measurement_col else row.get(measurement_col, "")

        def _s(v) -> str:
            s = "" if v is None else str(v)
            return "" if s.strip().lower() == "nan" else s.strip()

        kpis.append(
            KPI(
                name=name,
                target=_s(target),
                actual=_s(actual),
                measurement=_s(measurement) or "",
            )
        )

    return kpis, chosen
