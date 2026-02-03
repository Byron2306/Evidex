from __future__ import annotations

import csv
import json
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import request


@dataclass(frozen=True)
class AdVariant:
    campaign: str
    persona: str
    platform: str
    headline: str
    primary_text: str
    cta: str
    image_prompt: str
    disclaimer: str


DEFAULT_BRAND = {
    "service_name": "EVIDEX — Evidence Pack (48-Hour Delivery)",
    "country": "South Africa",
    "tone": "professional, concise, compliance-first",
    "promise": "audit-ready structure in 48 hours",
    "deliverables": [
        "Executive summary",
        "Evidence table (KPI → proof → sources)",
        "Narrative justification",
        "Sources folder (audit trail)",
        "Quality report",
    ],
    "disclaimer": "Not legal/tax advice. We package and structure documentation; you remain responsible for filings and final submissions.",
}


def _base_image_prompt(style: str = "clean") -> str:
    # Designed for SDXL. Keep consistent across personas.
    if style == "clean":
        return (
            "Minimal corporate design, navy and white palette, clean desk scene, a neat folder structure labeled "
            "'Executive Summary', 'Evidence Table', 'Narrative', 'Sources', 'Quality Report', subtle '48-hour delivery' badge, "
            "professional lighting, no people, no logos, high-resolution, modern B2B style"
        )
    return "Clean professional corporate compliance visual, no logos, high resolution"


def build_week1_variants(brand: dict[str, Any] | None = None) -> list[AdVariant]:
    b = {**DEFAULT_BRAND, **(brand or {})}

    deliverable_phrase = "; ".join(b["deliverables"][0:3]) + ", plus sources folder + QA report"
    img = _base_image_prompt("clean")
    disclaimer = b["disclaimer"]

    variants: list[AdVariant] = []

    # Campaign 1: Annual audit readiness (LinkedIn)
    variants.extend(
        [
            AdVariant(
                campaign="Audit Readiness (48h)",
                persona="CFO / Finance Director",
                platform="LinkedIn",
                headline="Audit-ready evidence pack in 48 hours",
                primary_text=(
                    "If your audit file is scattered across emails, PDFs, and spreadsheets, we turn it into a traceable evidence pack: "
                    f"{deliverable_phrase}. Fixed scope, done-for-you. {b['country']}-first."
                ),
                cta="Request sample structure",
                image_prompt=img,
                disclaimer=disclaimer,
            ),
            AdVariant(
                campaign="Audit Readiness (48h)",
                persona="Head of Internal Audit",
                platform="LinkedIn",
                headline="Make audit sampling painless",
                primary_text=(
                    "We package your evidence into a sampling-friendly folder structure with an evidence table (KPI/control → proof → sources). "
                    "Gaps are explicitly flagged. 48-hour delivery."
                ),
                cta="Book a 10-min fit check",
                image_prompt=img,
                disclaimer=disclaimer,
            ),
            AdVariant(
                campaign="Audit Readiness (48h)",
                persona="Compliance / Risk",
                platform="LinkedIn",
                headline="Evidence, structured. Risks, flagged.",
                primary_text=(
                    "Compliance reporting is only as good as the audit trail. We produce a traceable evidence pack with a QA report so you can see what’s strong and what’s missing."
                ),
                cta="Get pricing",
                image_prompt=img,
                disclaimer=disclaimer,
            ),
            AdVariant(
                campaign="Audit Readiness (48h)",
                persona="Finance Ops",
                platform="LinkedIn",
                headline="Stop chasing documents",
                primary_text=(
                    "Send your source files once. We return an organized pack: executive summary, evidence table, narrative, and a clean sources folder. Delivered in 48 hours."
                ),
                cta="Send a test job",
                image_prompt=img,
                disclaimer=disclaimer,
            ),
        ]
    )

    # Campaign 2: Tax/VAT urgency (Search + LinkedIn) — SA phrasing, SARS-safe
    variants.extend(
        [
            AdVariant(
                campaign="Tax/VAT Evidence (Urgent)",
                persona="Tax / VAT Manager",
                platform="Google Search",
                headline="VAT evidence pack (48h)",
                primary_text=(
                    "Reconcile VAT claims to source documents fast. We package invoices/receipts, summaries, and a traceable evidence table for internal review and SARS queries."
                ),
                cta="Get a callback",
                image_prompt=img,
                disclaimer=disclaimer,
            ),
            AdVariant(
                campaign="Tax/VAT Evidence (Urgent)",
                persona="CFO / Finance Director",
                platform="LinkedIn",
                headline="Tax/VAT evidence, ready for review",
                primary_text=(
                    "When deadlines hit, the real work is evidence. We produce a structured evidence pack (table + sources folder + QA report) in 48 hours."
                ),
                cta="Request turnaround slots",
                image_prompt=img,
                disclaimer=disclaimer,
            ),
            AdVariant(
                campaign="Tax/VAT Evidence (Urgent)",
                persona="Finance Ops",
                platform="LinkedIn",
                headline="Turn receipts into a review-ready pack",
                primary_text=(
                    "We organize receipts/invoices and summaries into a clean audit trail with a coverage report. Faster internal review, fewer missing-doc surprises."
                ),
                cta="See deliverable example",
                image_prompt=img,
                disclaimer=disclaimer,
            ),
            AdVariant(
                campaign="Tax/VAT Evidence (Urgent)",
                persona="Compliance / Risk",
                platform="LinkedIn",
                headline="Reduce evidence gaps before queries",
                primary_text=(
                    "We produce a conservative evidence pack: what’s supported, what’s missing, and where each claim is sourced. Delivered within 48 hours."
                ),
                cta="Talk to us",
                image_prompt=img,
                disclaimer=disclaimer,
            ),
        ]
    )

    # Campaign 3: Consultancy channel (LinkedIn)
    variants.extend(
        [
            AdVariant(
                campaign="Consultancy Partner (White-label)",
                persona="Grant / Audit Consultant",
                platform="LinkedIn",
                headline="White-label evidence packs (5/10 packs)",
                primary_text=(
                    "If you deliver audit support or donor reporting: resell structured evidence packs to your clients. "
                    "Batch pricing available (5/10). Clean ZIP, client-ready, your branding."
                ),
                cta="Request partner pricing",
                image_prompt=img,
                disclaimer=disclaimer,
            ),
            AdVariant(
                campaign="Consultancy Partner (White-label)",
                persona="Accounting / Advisory Firm",
                platform="LinkedIn",
                headline="Add a fast compliance deliverable",
                primary_text=(
                    "Offer a fixed-scope evidence pack alongside your advisory work: evidence table + sources folder + QA report. 48-hour turnaround support."
                ),
                cta="Become a partner",
                image_prompt=img,
                disclaimer=disclaimer,
            ),
            AdVariant(
                campaign="Consultancy Partner (White-label)",
                persona="ESG / CSI Consultancy",
                platform="LinkedIn",
                headline="KPI evidence packs your clients can trust",
                primary_text=(
                    "Help clients avoid overclaims: we build traceable KPI evidence packs with conservative narratives and explicit gaps. White-label delivery available."
                ),
                cta="Get partner deck",
                image_prompt=img,
                disclaimer=disclaimer,
            ),
            AdVariant(
                campaign="Consultancy Partner (White-label)",
                persona="Internal Audit Contractor",
                platform="LinkedIn",
                headline="Sampling-ready evidence in a ZIP",
                primary_text=(
                    "We structure messy client files into a sampling-friendly audit trail. You stay client-facing; we handle packaging. Batch options available."
                ),
                cta="Ask for batch rates",
                image_prompt=img,
                disclaimer=disclaimer,
            ),
        ]
    )

    return variants


def _tcp_ping(host: str, port: int, timeout_s: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def refine_copy_with_ollama(variants: list[AdVariant], *, base_url: str, model: str) -> list[AdVariant]:
    """Optional: refine copy using a local Ollama OpenAI-compatible endpoint.

    Expected base_url like: http://localhost:11434/v1
    """

    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return variants

    if not base_url:
        return variants

    # Basic liveness check (avoid long waits).
    try:
        host = base_url.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]
        port = int(base_url.split(":")[2].split("/")[0]) if ":" in base_url.replace("http://", "") else 80
        if not _tcp_ping(host, port):
            return variants
    except Exception:
        pass

    client = OpenAI(base_url=base_url, api_key=os.getenv("OLLAMA_API_KEY", "ollama"))

    out: list[AdVariant] = []
    for v in variants:
        system = "You are a senior B2B copywriter. You write concise, compliant ad copy." \
                 " Avoid exaggerated claims. Keep it South Africa-friendly." \
                 " Output JSON with keys: headline, primary_text, cta."
        user = (
            f"Persona: {v.persona}\nPlatform: {v.platform}\nCampaign: {v.campaign}\n\n"
            f"Draft headline: {v.headline}\nDraft primary text: {v.primary_text}\nDraft CTA: {v.cta}\n\n"
            "Constraints:\n"
            "- Keep headline <= 55 characters if possible\n"
            "- Keep primary text <= 280 characters (LinkedIn short)\n"
            "- Keep compliance disclaimer idea (not legal/tax advice) implicitly (don’t paste the disclaimer)\n"
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
                AdVariant(
                    campaign=v.campaign,
                    persona=v.persona,
                    platform=v.platform,
                    headline=str(data.get("headline", v.headline)).strip() or v.headline,
                    primary_text=str(data.get("primary_text", v.primary_text)).strip() or v.primary_text,
                    cta=str(data.get("cta", v.cta)).strip() or v.cta,
                    image_prompt=v.image_prompt,
                    disclaimer=v.disclaimer,
                )
            )
        except Exception:
            out.append(v)
        time.sleep(0.05)

    return out


def maybe_generate_images_automatic1111(
    variants: list[AdVariant],
    *,
    out_dir: Path,
    a1111_url: str,
    width: int = 1024,
    height: int = 1024,
    steps: int = 20,
    cfg_scale: float = 6.0,
) -> None:
    """Optional: generate images via AUTOMATIC1111 WebUI API.

    Requires the user to run WebUI with `--api`.
    """

    if not a1111_url:
        return

    payload_template = {
        "steps": steps,
        "cfg_scale": cfg_scale,
        "width": width,
        "height": height,
        "sampler_name": "DPM++ 2M Karras",
    }

    out_dir.mkdir(parents=True, exist_ok=True)

    for i, v in enumerate(variants, start=1):
        payload = dict(payload_template)
        payload["prompt"] = v.image_prompt
        req = request.Request(
            url=a1111_url.rstrip("/") + "/sdapi/v1/txt2img",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            images = data.get("images") or []
            if not images:
                continue
            # Base64 PNG
            import base64

            png_b64 = images[0]
            png_bytes = base64.b64decode(png_b64)
            fname = f"ad_{i:02d}_{v.platform.replace(' ', '_')}.png"
            (out_dir / fname).write_bytes(png_bytes)
        except Exception:
            continue


def write_outputs(variants: list[AdVariant], *, out_dir: Path, stem: str = "week1_ads") -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"{stem}.json"
    csv_path = out_dir / f"{stem}.csv"

    data = [
        {
            "campaign": v.campaign,
            "persona": v.persona,
            "platform": v.platform,
            "headline": v.headline,
            "primary_text": v.primary_text,
            "cta": v.cta,
            "image_prompt": v.image_prompt,
            "disclaimer": v.disclaimer,
        }
        for v in variants
    ]
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "campaign",
                "persona",
                "platform",
                "headline",
                "primary_text",
                "cta",
                "image_prompt",
                "disclaimer",
            ],
        )
        w.writeheader()
        for row in data:
            w.writerow(row)

    return json_path, csv_path
