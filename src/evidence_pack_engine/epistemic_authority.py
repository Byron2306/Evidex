from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

MIN_CANDIDATE_MATCH = 0.50


class HumanReviewRequired(RuntimeError):
    pass


def _load_spine():
    candidates = []
    env_dir = str(os.getenv("DIO_EPISTEMIC_SPINE_DIR") or "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    candidates.append(Path.home() / "DIO-Full-Audit" / "scripts")
    for candidate in candidates:
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
    try:
        spine = importlib.import_module("dio_epistemic_spine")
    except Exception as exc:
        raise RuntimeError(
            "DIO epistemic spine unavailable; Evidex evidence authority is refused."
        ) from exc
    if not callable(getattr(spine, "claim_epistemic_state", None)):
        raise RuntimeError(
            "DIO epistemic spine missing claim_epistemic_state primitive."
        )
    return spine


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _meaningful_actual(value: Any) -> bool:
    text = _norm(value)
    return bool(text) and text not in {
        "n/a", "na", "none", "unknown", "tbd",
        "not available", "not applicable", "-"
    }


def _actual_present(actual: str, source_text: str) -> bool:
    actual_norm = _norm(actual)
    source_norm = _norm(source_text)
    if not actual_norm or not source_norm:
        return False
    if re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?%?", actual_norm):
        token = re.escape(actual_norm.replace(",", "."))
        source_num = source_norm.replace(",", ".")
        return bool(
            re.search(rf"(?<![\d.]){token}(?![\d.])", source_num)
        )
    return actual_norm in source_norm


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _input_fingerprint(job_dir: Path) -> str:
    job_dir = Path(job_dir)
    targets = []
    intake = job_dir / "intake.yaml"
    if intake.is_file():
        targets.append(intake)
    uploads = job_dir / "uploads"
    if uploads.is_dir():
        targets.extend(sorted(p for p in uploads.rglob("*") if p.is_file()))

    h = hashlib.sha256()
    for path in targets:
        try:
            rel = path.relative_to(job_dir).as_posix()
        except Exception:
            rel = path.name
        h.update(rel.encode("utf-8", errors="ignore"))
        h.update(b"\0")
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()


def _source_receipt(doc: Any, actual: str, match_score: float) -> dict[str, Any]:
    text = str(getattr(doc, "text", "") or "")
    path = getattr(doc, "path", None)
    name = str(getattr(path, "name", path) or "")
    present = _actual_present(actual, text)

    exact_span = ""
    if present:
        raw_actual = str(actual or "").strip()
        pos = text.casefold().find(raw_actual.casefold())
        if pos >= 0:
            start = max(0, pos - 140)
            end = min(len(text), pos + len(raw_actual) + 140)
            exact_span = re.sub(r"\s+", " ", text[start:end]).strip()

    return {
        "source": name,
        "match_score": float(match_score),
        "actual_value_present": present,
        "exact_span": exact_span,
        "source_text_sha256": hashlib.sha256(
            text.encode("utf-8", errors="ignore")
        ).hexdigest(),
        "authorized": bool(
            match_score >= MIN_CANDIDATE_MATCH and present
        ),
    }


def evaluate_kpi_evidence(kpi: Any, scored_docs: list[tuple[float, Any]]) -> dict[str, Any]:
    spine = _load_spine()
    actual = str(getattr(kpi, "actual", "") or "")
    candidates = []

    for score, doc in scored_docs:
        try:
            score_value = max(0.0, float(score or 0.0))
        except Exception:
            score_value = 0.0
        if score_value < MIN_CANDIDATE_MATCH:
            continue
        candidates.append(_source_receipt(doc, actual, score_value))

    authorized = [r for r in candidates if r["authorized"]]

    if authorized:
        state = spine.claim_epistemic_state(support_count=len(authorized))
    elif candidates:
        state = spine.claim_epistemic_state()
    else:
        state = spine.claim_epistemic_state(outside_available_evidence=True)

    reasons = []
    if not _meaningful_actual(actual):
        reasons.append("declared_actual_value_missing_or_non_specific")
    if not candidates:
        reasons.append("no_candidate_source_clears_relevance_floor")
    elif not authorized:
        reasons.append("candidate_sources_do_not_ground_declared_actual")

    return {
        "schema": "dio.evidex.kpi_evidence_authority.v1",
        "epistemic_state": state,
        "passed": bool(authorized) and _meaningful_actual(actual),
        "candidate_sources": [r["source"] for r in candidates],
        "authorized_sources": [r["source"] for r in authorized],
        "source_receipts": candidates,
        "reasons": reasons,
        "keyword_overlap_is_authority": False,
    }


def summarize_pack_authority(rows: list[Any], flags: list[str]) -> dict[str, Any]:
    receipts = []
    all_supported = bool(rows)
    for row in rows:
        state = str(getattr(row, "epistemic_state", "") or "")
        authorized = list(getattr(row, "authorized_sources", []) or [])
        passed = state == "SUPPORTED" and bool(authorized)
        all_supported = all_supported and passed
        receipts.append({
            "kpi_name": str(getattr(row, "kpi_name", "") or ""),
            "epistemic_state": state or "UNVERIFIED",
            "authorized_sources": authorized,
            "candidate_sources": list(
                getattr(row, "candidate_sources", []) or []
            ),
            "passed": passed,
        })

    return {
        "schema": "dio.evidex.pack_authority.v1",
        "pack_compilation_allowed": True,
        "evidence_authority_passed": all_supported,
        "human_approval_required": True,
        "delivery_authorized": False,
        "status": "NEEDS_HUMAN_REVIEW",
        "flags": list(flags or []),
        "rows": receipts,
        "law": "pack compilation is not delivery release",
    }


def stage_pending_release(*, job_dir: Path, pack_root: Path, zip_path: Path) -> dict[str, Any]:
    job_dir = Path(job_dir)
    pack_root = Path(pack_root)
    zip_path = Path(zip_path)
    authority_path = pack_root / "EPISTEMIC_AUTHORITY.json"

    if not authority_path.is_file():
        raise RuntimeError("Compiled pack is missing EPISTEMIC_AUTHORITY.json.")

    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    pending = {
        "schema": "dio.evidex.pending_release.v1",
        "zip_path": str(zip_path.resolve()),
        "pack_root": str(pack_root.resolve()),
        "deliverable_sha256": _sha256_file(zip_path),
        "authority_receipt_sha256": _sha256_file(authority_path),
        "input_fingerprint": _input_fingerprint(job_dir),
        "evidence_authority_passed": bool(
            authority.get("evidence_authority_passed")
        ),
        "human_approval_required": True,
        "delivery_authorized": False,
    }
    (job_dir / "PENDING_EPISTEMIC_RELEASE.json").write_text(
        json.dumps(pending, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return pending


def _load_pending(job_dir: Path) -> dict[str, Any] | None:
    path = Path(job_dir) / "PENDING_EPISTEMIC_RELEASE.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def clear_stale_release_state(job_dir: Path) -> None:
    for name in (
        "PENDING_EPISTEMIC_RELEASE.json",
        "HUMAN_DECISION.json",
        "HUMAN_DECISION_READY.txt",
        "EPISTEMIC_RELEASE.json",
        "NEEDS_HUMAN_REVIEW.txt",
    ):
        try:
            (Path(job_dir) / name).unlink()
        except FileNotFoundError:
            pass


def evaluate_pending_release(job_dir: Path) -> dict[str, Any]:
    job_dir = Path(job_dir)
    pending = _load_pending(job_dir)
    if pending is None:
        return {
            "delivery_authorized": False,
            "status": "NO_PENDING_RELEASE",
            "reasons": ["pending_release_receipt_missing"],
        }

    reasons = []
    zip_path = Path(str(pending.get("zip_path") or ""))
    pack_root = Path(str(pending.get("pack_root") or ""))
    authority_path = pack_root / "EPISTEMIC_AUTHORITY.json"

    if _input_fingerprint(job_dir) != pending.get("input_fingerprint"):
        reasons.append("job_inputs_changed_since_compilation")

    if not zip_path.is_file():
        reasons.append("compiled_zip_missing")
    elif _sha256_file(zip_path) != pending.get("deliverable_sha256"):
        reasons.append("compiled_zip_hash_changed")

    if not authority_path.is_file():
        reasons.append("authority_receipt_missing")
    elif _sha256_file(authority_path) != pending.get("authority_receipt_sha256"):
        reasons.append("authority_receipt_hash_changed")

    if not pending.get("evidence_authority_passed"):
        reasons.append("evidence_authority_not_satisfied")

    decision_path = job_dir / "HUMAN_DECISION.json"
    decision = None
    if not decision_path.is_file():
        reasons.append("human_decision_missing")
    else:
        try:
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
        except Exception:
            reasons.append("human_decision_unreadable")

    if decision is not None:
        if str(decision.get("decision") or "").upper() != "APPROVE":
            reasons.append("human_decision_not_approve")
        if not str(decision.get("approved_by") or "").strip():
            reasons.append("human_approver_missing")
        if not str(decision.get("approved_at") or "").strip():
            reasons.append("human_approval_timestamp_missing")
        if decision.get("deliverable_sha256") != pending.get("deliverable_sha256"):
            reasons.append("human_decision_zip_hash_mismatch")
        if decision.get("authority_receipt_sha256") != pending.get("authority_receipt_sha256"):
            reasons.append("human_decision_authority_hash_mismatch")
        if decision.get("input_fingerprint") != pending.get("input_fingerprint"):
            reasons.append("human_decision_input_fingerprint_mismatch")

    return {
        "schema": "dio.evidex.release_authority.v1",
        "delivery_authorized": not reasons,
        "status": "AUTHORIZED" if not reasons else "NEEDS_HUMAN_REVIEW",
        "reasons": reasons,
        "pending": pending,
        "human_decision": decision,
    }


def resume_pending_release(job_dir: Path) -> Path | None:
    job_dir = Path(job_dir)
    pending = _load_pending(job_dir)
    if pending is None:
        return None

    if _input_fingerprint(job_dir) != pending.get("input_fingerprint"):
        clear_stale_release_state(job_dir)
        return None

    release = evaluate_pending_release(job_dir)
    (job_dir / "EPISTEMIC_RELEASE.json").write_text(
        json.dumps(release, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not release.get("delivery_authorized"):
        raise HumanReviewRequired(
            "Compiled pack is awaiting a valid human release decision."
        )

    zip_path = Path(pending["zip_path"])
    deliverable = job_dir / "DELIVERABLE.zip"
    if deliverable.exists():
        deliverable.unlink()
    shutil.copy2(zip_path, deliverable)
    (job_dir / "DELIVERABLE_READY.txt").write_text(
        "Deliverable released after evidence-authority and human-decision validation.\n",
        encoding="utf-8",
    )
    for marker in ("NEEDS_HUMAN_REVIEW.txt", "HUMAN_DECISION_READY.txt"):
        try:
            (job_dir / marker).unlink()
        except FileNotFoundError:
            pass
    return zip_path
