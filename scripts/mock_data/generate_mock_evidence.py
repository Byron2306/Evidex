from __future__ import annotations

import argparse
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from docx import Document


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_docx(path: Path, title: str, paragraphs: list[str]) -> None:
    doc = Document()
    doc.add_heading(title, level=1)
    for p in paragraphs:
        doc.add_paragraph(p)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))


def _rng(seed: str) -> random.Random:
    return random.Random(seed)


def generate_grant_reporting_mock_uploads(
    *,
    uploads_dir: Path,
    org: str = "ACME NGO",
    project: str = "Health Outreach",
    period_start: str = "2025-10-01",
    period_end: str = "2025-12-31",
) -> None:
    _ensure_dir(uploads_dir)
    r = _rng(f"{org}|{project}|{period_start}|{period_end}")

    # 1) Field visit forms (CSV)
    start = date.fromisoformat(period_start)
    end = date.fromisoformat(period_end)
    days = (end - start).days

    rows = []
    total_visits = r.randint(40, 70)
    households_total = 0

    for i in range(total_visits):
        d = start + timedelta(days=r.randint(0, max(1, days)))
        households = r.randint(10, 60)
        households_total += households
        rows.append(
            {
                "date": d.isoformat(),
                "site": r.choice(["Ward A", "Ward B", "Ward C", "Ward D"]),
                "team": r.choice(["Team 1", "Team 2", "Team 3"]),
                "households_visited": households,
                "notes": r.choice(
                    [
                        "Routine outreach and referrals",
                        "Follow-up visit; updated household register",
                        "Community mobilization and screening",
                        "Health education session + referrals",
                    ]
                ),
            }
        )

    df_visits = pd.DataFrame(rows).sort_values("date")
    df_visits.to_csv(uploads_dir / "field_visit_forms.csv", index=False)

    # 2) Facility support checklist (DOCX)
    _write_docx(
        uploads_dir / "facility_support_checklists.docx",
        f"Facility Support Checklists — {project}",
        [
            f"Organization: {org}",
            f"Reporting period: {period_start} to {period_end}",
            "Summary: This document consolidates signed facility checklists for supported clinics.",
            "Supported clinics (sample): Clinic 01, Clinic 02, Clinic 03, Clinic 04, Clinic 05.",
            "Checklist items (example): cold-chain verified, stock cards updated, triage protocols posted, referral log reviewed.",
        ],
    )

    # 3) Supervisor summary notes (TXT) — intentionally missing one month sometimes
    sup_months = ["October", "December"]
    (uploads_dir / "supervisor_summary_october.txt").write_text(
        "Supervisor Summary — October\n\nActivities: outreach planning, team supervision, data spot-checks.\n"
        "Notes: Household visit forms reviewed; minor corrections made to site codes.\n",
        encoding="utf-8",
    )
    (uploads_dir / "supervisor_summary_december.txt").write_text(
        "Supervisor Summary — December\n\nActivities: endline prep, clinic follow-ups, referral verification.\n"
        "Notes: Reconciled visit totals against registers; confirmed referral documentation for sample cases.\n",
        encoding="utf-8",
    )
    (uploads_dir / "supervisor_summary_NOTE_missing_november.txt").write_text(
        "NOTE: November supervisor summary is missing (mock gap for testing flags).\n",
        encoding="utf-8",
    )

    # 4) Finance summary (XLSX)
    categories = [
        "Personnel",
        "Transport",
        "Supplies",
        "Facility support",
        "Training",
        "Monitoring",
    ]
    budget = [r.randint(8000, 15000) for _ in categories]
    actual = [int(b * r.uniform(0.75, 1.05)) for b in budget]
    df_fin = pd.DataFrame({"Category": categories, "Budget": budget, "Actual": actual})
    df_fin["Variance"] = df_fin["Budget"] - df_fin["Actual"]
    with pd.ExcelWriter(uploads_dir / "finance_summary.xlsx") as xw:
        df_fin.to_excel(xw, sheet_name="Summary", index=False)
        pd.DataFrame(
            {
                "Assumption": [
                    "All figures are illustrative mock data",
                    "Receipts available upon request",
                ]
            }
        ).to_excel(xw, sheet_name="Notes", index=False)

    # 5) Meeting minutes (DOCX)
    _write_docx(
        uploads_dir / "coordination_meeting_minutes.docx",
        "Coordination Meeting Minutes",
        [
            f"Project: {project}",
            "Attendees: Program Lead, M&E Officer, Finance, Field Supervisor",
            "Agenda: progress review, KPI tracking, risks, next steps",
            "Decisions: prioritize backlog of visit form reviews; schedule clinic follow-ups.",
        ],
    )

    # 6) KPI sheet (CSV) aligned to sample job KPIs
    # For the ACME sample job we want two KPIs: clinics supported, households visited.
    df_kpis = pd.DataFrame(
        [
            {
                "kpi": "Clinics supported",
                "target": 12,
                "actual": 11,
                "measurement": "Facility support logs + signed checklists",
                "evidence": "facility_support_checklists.docx",
            },
            {
                "kpi": "Households visited",
                "target": 1500,
                "actual": households_total,
                "measurement": "Field visit forms + supervisor summaries",
                "evidence": "field_visit_forms.csv; supervisor_summary_october.txt; supervisor_summary_december.txt",
            },
        ]
    )
    df_kpis.to_csv(uploads_dir / "kpi_sheet.csv", index=False)

    # 7) Email thread (TXT)
    (uploads_dir / "email_thread_mock.txt").write_text(
        "From: donor.program@foundation.org\n"
        "To: grants@acme.org\n"
        "Subject: Q4 reporting reminder\n\n"
        "Hi team,\nPlease ensure the Q4 report includes KPI evidence references and any material variances.\n\n"
        "Thanks,\nProgram Officer\n",
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uploads", required=True, help="Path to uploads folder to populate")
    ap.add_argument("--org", default="ACME NGO")
    ap.add_argument("--project", default="Health Outreach")
    ap.add_argument("--start", default="2025-10-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()

    generate_grant_reporting_mock_uploads(
        uploads_dir=Path(args.uploads),
        org=args.org,
        project=args.project,
        period_start=args.start,
        period_end=args.end,
    )
    print(f"Mock uploads created in: {Path(args.uploads).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
