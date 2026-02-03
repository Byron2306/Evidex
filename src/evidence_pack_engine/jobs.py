from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .pack import load_intake, write_pack
from .errors import TransientJobError, is_transient_file_error, with_retries


@dataclass(frozen=True)
class JobPaths:
    root: Path
    intake_path: Path
    uploads_dir: Path


def _env_truthy(name: str, default: str = "") -> bool:
    v = os.getenv(name, default)
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def is_payment_required() -> bool:
    """If true, jobs only process once PAID.txt exists in the job folder."""

    return _env_truthy("REQUIRE_PAYMENT", "0")


def is_job_paid(job_dir: Path) -> bool:
    return (job_dir / "PAID.txt").exists()


def is_job_ready(job_dir: Path) -> bool:
    # If the watcher previously marked this job for retry (e.g., Drive/OneDrive lock),
    # do not treat it as ready while it still sits under incoming/. This prevents
    # rapid re-triggers that can lead to duplicate/cloned folders.
    if (job_dir / "RETRY_LATER.txt").exists():
        return False

    intake = job_dir / "intake.yaml"
    uploads = job_dir / "uploads"
    if not (intake.exists() and uploads.exists() and uploads.is_dir()):
        return False

    # Ensure intake.yaml is actually readable (Drive/OneDrive placeholders can exist but not be hydrated yet).
    try:
        with intake.open("rb") as f:
            _ = f.read(1)
    except OSError as e:
        if is_transient_file_error(e):
            return False
        return False

    if is_payment_required() and not is_job_paid(job_dir):
        return False

    # Require at least one non-placeholder file in uploads/.
    # Prevent premature processing while files are still syncing.
    real_files: list[Path] = []
    for p in uploads.rglob("*"):
        if not p.is_file():
            continue
        name = p.name.lower()
        if name in {"readme.txt", ".keep", ".gitkeep", "desktop.ini", "thumbs.db"}:
            continue
        real_files.append(p)

    if not real_files:
        return False

    # If any upload was modified very recently, assume sync is still in progress.
    now = time.time()
    for p in real_files:
        try:
            if now - p.stat().st_mtime < 5.0:
                return False
        except OSError:
            return False

    # If any file is locked (Windows sharing violation), delay processing.
    for p in real_files:
        try:
            with p.open("rb") as _f:
                _f.read(1)
        except OSError as e:
            if is_transient_file_error(e):
                return False
            return False

    return True


def wait_for_stable(job_dir: Path, *, seconds: float = 2.0) -> None:
    """Wait briefly for file writes/sync to settle."""
    def snapshot() -> tuple[int, int]:
        files = [p for p in job_dir.rglob("*") if p.is_file()]
        try:
            total_size = sum(p.stat().st_size for p in files)
        except OSError as e:
            if is_transient_file_error(e):
                raise TransientJobError(str(e))
            raise
        return len(files), total_size

    a = snapshot()
    time.sleep(seconds)
    b = snapshot()
    if a != b:
        time.sleep(seconds)


def process_job(*, job_dir: Path, deliveries_dir: Path) -> Path:
    """Generate the pack zip for a job folder.

    Expected structure:
      job_dir/
        intake.yaml
        uploads/
          ...
    """
    if not is_job_ready(job_dir):
        reason = "missing intake.yaml or uploads/"
        if is_payment_required() and not is_job_paid(job_dir):
            reason = "waiting for payment (PAID.txt)"
        raise ValueError(f"Job not ready ({reason}): {job_dir}")

    wait_for_stable(job_dir, seconds=2.0)

    deliveries_dir.mkdir(parents=True, exist_ok=True)
    intake_path = job_dir / "intake.yaml"
    try:
        zip_path, pack_root, flags = write_pack(
            intake_path=intake_path,
            uploads_dir=job_dir / "uploads",
            out_dir=deliveries_dir,
            job_name=job_dir.name,
        )
    except OSError as e:
        if is_transient_file_error(e):
            raise TransientJobError(str(e))
        raise

    # Invoice + delivery email are generated inside write_pack (and included in the ZIP).

    # Ensure job folder contains a minimal set of delivery artifacts for downstream automations.
    # The Drive delivery emailer specifically needs CONTACT_EMAIL.txt + DELIVERABLE.zip.
    try:
        intake = load_intake(intake_path)
        contact = str(getattr(intake.client, "contact_email", "") or "").strip()
        if contact:
            (job_dir / "CONTACT_EMAIL.txt").write_text(contact + "\n", encoding="utf-8")
    except Exception:
        pass

    try:
        email_src = pack_root / "DELIVERY_EMAIL.txt"
        email_dst = job_dir / "DELIVERY_EMAIL.txt"
        if email_src.exists() and not email_dst.exists():
            shutil.copy2(email_src, email_dst)
    except Exception:
        pass

    # Convenience: drop the ZIP into the job folder (Drive-synced) so email/payment automations
    # can link to a single folder without needing to locate deliveries/.
    deliverable_path = job_dir / "DELIVERABLE.zip"

    def _copy_deliverable() -> None:
        if deliverable_path.exists():
            deliverable_path.unlink()
        shutil.copy2(zip_path, deliverable_path)

    try:
        with_retries(_copy_deliverable, attempts=8)
        (job_dir / "DELIVERABLE_READY.txt").write_text(
            "Deliverable created: DELIVERABLE.zip\n",
            encoding="utf-8",
        )
    except Exception:
        # Non-fatal: the deliverable still exists in deliveries/.
        pass

    return zip_path


def move_dir(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.move(str(src), str(dst))
