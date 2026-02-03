from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ScheduledAd:
    day: str  # YYYY-MM-DD
    stream: str  # A/B/C
    stream_name: str
    moment: str  # Before scrutiny / During scrutiny / For approval & legitimacy
    core_message: str
    platform: str  # LinkedIn/Locanto/MyAdz
    persona: str
    template_image: str  # absolute path
    headline: str
    tagline: str
    primary_text: str
    cta: str
    links: dict[str, str]


DEFAULT_STREAMS: dict[str, dict[str, Any]] = {
    "A": {
        "name": "Campaign A — Compliance readiness",
        "moment": "Before scrutiny",
        "core_message": "Be ready before anyone asks. When the audit comes, nothing will be missing.",
        "personas": [
            "CFO / Finance Director",
            "Finance Manager",
            "Finance Ops",
            "Head of Internal Audit",
            "Compliance / Risk",
            "Risk Officer",
            "Internal Audit Manager",
            "Audit Committee Secretariat",
        ],
    },
    "B": {
        "name": "Campaign B — Regulatory response & scrutiny",
        "moment": "During scrutiny",
        "core_message": "Respond correctly. Don’t escalate. You can respond without panic — and be taken seriously.",
        "personas": [
            "Tax / VAT Manager",
            "CFO / Finance Director",
            "Finance Ops",
            "Compliance / Risk",
            "Finance Manager",
            "SME Owner / Director",
            "Accounting firm (client support)",
        ],
    },
    "C": {
        "name": "Campaign C — Approval, funding & performance",
        "moment": "For approval & legitimacy",
        "core_message": "Evidence that gets approved. Your evidence is coherent, defensible, and approval-ready.",
        "personas": [
            "NGO Director / M&E",
            "University research office",
            "Grant consultancy",
            "ESG / CSI lead",
            "Impact / reporting manager",
            "Academic promotion candidate",
            "Board / senior decision-maker",
            "Grant / Audit Consultant",
            "Accounting / Advisory Firm",
            "ESG / CSI Consultancy",
        ],
    },
}


DEFAULT_PLATFORMS: list[str] = ["LinkedIn", "Locanto", "MyAdz"]


def _platform_for_day(*, platforms: list[str], day_index: int, stream_index: int) -> str:
    if not platforms:
        return "LinkedIn"
    # Rotate platforms across days, offset per stream so A/B/C aren’t identical.
    return platforms[(day_index + stream_index) % len(platforms)]


def _env_link(name: str, fallback: str) -> str:
    return (os.getenv(name) or fallback).strip()


def default_links() -> dict[str, str]:
    return {
        "locanto": _env_link("EVIDEX_LINK_LOCANTO", "https://www.locanto.co.za/by/bboy2306/d10815/"),
        "myadz": _env_link("EVIDEX_LINK_MYADZ", "https://myadz.co.za/post-free-ad/"),
        "linkedin": _env_link("EVIDEX_LINK_LINKEDIN", "https://www.linkedin.com/company/evidex23"),
    }


def _list_templates(dir_path: Path) -> list[Path]:
    if not dir_path.exists():
        return []
    out: list[Path] = []
    for p in sorted(dir_path.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            out.append(p)
    return out


def _base_copy(*, stream: str, persona: str, platform: str, links: dict[str, str]) -> tuple[str, str, str, str]:
    # headline, tagline, primary_text, cta
    if stream == "A":
        headline = "Be audit-ready before anyone asks"
        tagline = "Compliance-ready evidence, always."
        body = (
            "Pre-assemble your evidence packs so audits and reviews are smooth: categorized folders, policy/procedure docs, "
            "periodic snapshots, and an audit-friendly evidence map. When scrutiny arrives, nothing is missing."
        )
        cta = "Request sample structure"
    elif stream == "B":
        headline = "Respond to regulators without panic"
        tagline = "Calm, defensible response packs."
        body = (
            "If you’ve received a request (SARS/regulator/audit), we triage and re-package your documents into a coherent response pack: "
            "evidence extraction, gap flags, status tracking, and submission-ready formatting."
        )
        cta = "Get a callback"
    else:
        headline = "Evidence that gets approved"
        tagline = "Coherent, defensible, panel-ready."
        body = (
            "For grants, ESG/impact, accreditation or performance review: we map evidence to criteria, assemble a clean portfolio, "
            "and produce summaries that help decision-makers approve confidently. White-label delivery available."
        )
        cta = "Request a sample pack"

    # Light persona/platform personalization
    who = (persona or "").strip()
    if who:
        body = f"For {who}: " + body

    if platform == "LinkedIn":
        # keep it tighter
        body = body.replace("48-hour turnaround", "48h turnaround")
    elif platform == "Locanto":
        body += (
            f"\n\nLinks: LinkedIn {links['linkedin']} | Locanto {links['locanto']} | MyAdz {links['myadz']}"
        )
    else:
        body += f"\n\nMore: {links['linkedin']}"

    return headline, tagline, body, cta


def _refine_with_ollama(items: list[ScheduledAd], *, base_url: str, model: str) -> list[ScheduledAd]:
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return items

    if not base_url:
        return items

    client = OpenAI(base_url=base_url, api_key=os.getenv("OLLAMA_API_KEY", "ollama"))

    out: list[ScheduledAd] = []
    for it in items:
        system = (
            "You are a senior B2B copywriter for a compliance documentation service in South Africa. "
            "Write concise, conservative ad copy. Avoid exaggerated claims and guarantees beyond '48-hour delivery'. "
            "Return ONLY JSON with keys: headline, tagline, primary_text, cta."
        )
        user = (
            f"Stream: {it.stream} ({it.stream_name})\n"
            f"Platform: {it.platform}\n"
            f"Persona: {it.persona}\n"
            f"Draft headline: {it.headline}\n"
            f"Draft tagline: {it.tagline}\n"
            f"Draft primary_text: {it.primary_text}\n"
            f"Draft CTA: {it.cta}\n\n"
            "Constraints:\n"
            "- Headline <= 60 chars if possible\n"
            "- Tagline <= 60 chars\n"
            "- Primary text: LinkedIn <= 280 chars; Locanto/MyAdz <= 600 chars\n"
            "- Keep the included links (don’t remove URLs if present).\n"
            "Return only JSON."
        )

        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.5,
            )
            content = (resp.choices[0].message.content or "").strip()
            data = json.loads(content)
            out.append(
                ScheduledAd(
                    day=it.day,
                    stream=it.stream,
                    stream_name=it.stream_name,
                    platform=it.platform,
                    persona=it.persona,
                    template_image=it.template_image,
                    headline=str(data.get("headline", it.headline)).strip() or it.headline,
                    tagline=str(data.get("tagline", it.tagline)).strip() or it.tagline,
                    primary_text=str(data.get("primary_text", it.primary_text)).strip() or it.primary_text,
                    cta=str(data.get("cta", it.cta)).strip() or it.cta,
                    links=it.links,
                )
            )
        except Exception:
            out.append(it)

        time.sleep(0.03)

    return out


def generate_rotation(
    *,
    start: date,
    days: int = 10,
    streams: list[str] | None = None,
    platforms: list[str] | None = None,
    mode: str = "one_per_stream_per_day",
    campaign_dirs: dict[str, Path] | None = None,
    refine: bool = False,
    ollama_base_url: str | None = None,
    ollama_model: str | None = None,
) -> list[ScheduledAd]:
    streams = streams or ["A", "B", "C"]
    platforms = platforms or list(DEFAULT_PLATFORMS)
    links = default_links()

    repo_root = Path(__file__).resolve().parents[2]
    campaign_dirs = campaign_dirs or {
        "A": repo_root / "marketing" / "campaign A",
        "B": repo_root / "marketing" / "campaign B",
        "C": repo_root / "marketing" / "campaign C",
    }

    templates: dict[str, list[Path]] = {k: _list_templates(v) for k, v in campaign_dirs.items()}

    items: list[ScheduledAd] = []
    for d in range(max(1, int(days))):
        day = start + timedelta(days=d)
        for s_idx, s in enumerate(streams):
            stream_def = DEFAULT_STREAMS.get(s) or {"name": s, "personas": ["General"]}
            personas: list[str] = list(stream_def.get("personas") or ["General"])
            persona = personas[d % len(personas)] if personas else "General"

            moment = str(stream_def.get("moment") or "")
            core_message = str(stream_def.get("core_message") or "")

            imgs = templates.get(s) or []
            img = imgs[d % len(imgs)] if imgs else Path("")

            if mode == "all_platforms":
                chosen_platforms = list(platforms)
            else:
                chosen_platforms = [_platform_for_day(platforms=platforms, day_index=d, stream_index=s_idx)]

            for platform in chosen_platforms:
                headline, tagline, body, cta = _base_copy(stream=s, persona=persona, platform=platform, links=links)
                items.append(
                    ScheduledAd(
                        day=day.isoformat(),
                        stream=s,
                        stream_name=str(stream_def.get("name") or s),
                        moment=moment,
                        core_message=core_message,
                        platform=platform,
                        persona=persona,
                        template_image=str(img.resolve()) if img else "",
                        headline=headline,
                        tagline=tagline,
                        primary_text=body,
                        cta=cta,
                        links=links,
                    )
                )

    if refine:
        base_url = (ollama_base_url or os.getenv("OLLAMA_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").strip() or "http://localhost:11434/v1"
        model = (
            ollama_model
            or os.getenv("LLM_MODEL_MARKETING")
            or os.getenv("OPENAI_MODEL_MARKETING")
            or os.getenv("OLLAMA_MODEL_MARKETING")
            or os.getenv("OLLAMA_MODEL")
            or "llama3.1:8b"
        )
        items = _refine_with_ollama(items, base_url=base_url, model=str(model))

    return items


def write_rotation_outputs(*, items: list[ScheduledAd], out_dir: Path, stem: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.json"
    csv_path = out_dir / f"{stem}.csv"

    data = [asdict(x) for x in items]
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "day",
                "stream",
                "stream_name",
                "moment",
                "core_message",
                "platform",
                "persona",
                "template_image",
                "headline",
                "tagline",
                "primary_text",
                "cta",
                "links",
            ],
        )
        w.writeheader()
        for row in data:
            row = dict(row)
            row["links"] = json.dumps(row.get("links") or {}, ensure_ascii=False)
            w.writerow(row)

    return json_path, csv_path
