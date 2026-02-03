from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .errors import TransientJobError, with_retries
from .jobs import is_job_ready, process_job


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong(0)
                if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) == 0:
                    return False
                # STILL_ACTIVE == 259
                return int(code.value) == 259
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)

        # POSIX best-effort
        os.kill(pid, 0)
        return True
    except Exception:
        return False


class WatcherAlreadyRunningError(RuntimeError):
    pass


def _acquire_watcher_lock(cfg: "WatchConfig") -> Path:
    """Create an exclusive lock file under the watch root to prevent duplicate watchers."""

    # watch root is the parent folder of incoming/
    root = cfg.incoming_dir.parent
    lock_path = root / ".evidex_watcher.lock"

    # Allow disabling via env for power users.
    if str(os.getenv("WATCHER_DISABLE_LOCK", "") or "").strip().lower() in {"1", "true", "yes", "y", "on"}:
        return lock_path

    # Fast path: create lock exclusively
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(str(lock_path), flags)
        try:
            payload = f"pid={os.getpid()}\nstarted={time.strftime('%Y-%m-%dT%H:%M:%S')}\nroot={root}\n"
            os.write(fd, payload.encode("utf-8", errors="ignore"))
        finally:
            os.close(fd)
        return lock_path
    except FileExistsError:
        # Possible stale lock; check pid
        try:
            txt = lock_path.read_text(encoding="utf-8", errors="ignore")
            pid = 0
            for line in txt.splitlines():
                if line.startswith("pid="):
                    try:
                        pid = int(line.split("=", 1)[1].strip())
                    except Exception:
                        pid = 0
                    break
            if pid and _pid_is_alive(pid):
                raise WatcherAlreadyRunningError(
                    f"Watcher already running (pid {pid}) for watch root: {root}. Stop it before starting another."
                )
        except WatcherAlreadyRunningError:
            raise
        except Exception:
            # If we can't read/parse, treat as active to be safe.
            raise WatcherAlreadyRunningError(
                f"Watcher lock exists at {lock_path}. Remove it only if you're sure no watcher is running."
            )

        # Stale lock: remove and retry once
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            raise WatcherAlreadyRunningError(
                f"Watcher lock exists at {lock_path} and could not be removed."
            )
        return _acquire_watcher_lock(cfg)


def _move_attempts() -> int:
    raw = str(os.getenv("WATCHER_MOVE_ATTEMPTS", "12") or "12").strip()
    try:
        n = int(raw)
    except Exception:
        n = 12
    return max(1, n)


def _clear_job_markers(job_dir: Path) -> None:
    for name in ("ERROR.txt", "RETRY_LATER.txt"):
        try:
            (job_dir / name).unlink(missing_ok=True)
        except Exception:
            pass


def _trash_dir(cfg: WatchConfig) -> Path:
    # cfg.incoming_dir is <root>/incoming
    return cfg.incoming_dir.parent / "_trash"


def _merge_dirs_best_effort(src: Path, dst: Path) -> None:
    """Best-effort merge of src into dst without overwriting existing files."""
    try:
        dst.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    for p in src.rglob("*"):
        try:
            rel = p.relative_to(src)
        except Exception:
            continue

        target = dst / rel
        try:
            if p.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not p.is_file():
                continue
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
        except Exception:
            # Ignore per-file errors (locks, hydration, etc.)
            continue


def _max_concurrency() -> int:
    raw = str(os.getenv("WATCHER_MAX_CONCURRENCY", "1") or "1").strip()
    try:
        n = int(raw)
    except Exception:
        n = 1
    return max(1, n)


_JOB_SEM = threading.BoundedSemaphore(_max_concurrency())


@dataclass
class WatchConfig:
    incoming_dir: Path
    processing_dir: Path
    done_dir: Path
    failed_dir: Path
    deliveries_dir: Path


class _Handler(FileSystemEventHandler):
    def __init__(self, cfg: WatchConfig):
        self._cfg = cfg
        self._lock = threading.Lock()
        self._seen: set[Path] = set()
        self._last_scan = 0.0

    def on_created(self, event):
        self._maybe_trigger(event)

    def on_modified(self, event):
        self._maybe_trigger(event)

    def _maybe_trigger(self, event):
        try:
            path = Path(event.src_path)
        except Exception:
            return

        # We only care about job folders directly under incoming.
        try:
            rel = path.relative_to(self._cfg.incoming_dir)
        except Exception:
            return

        job_dir = self._cfg.incoming_dir / rel.parts[0] if rel.parts else self._cfg.incoming_dir
        if job_dir == self._cfg.incoming_dir:
            return

        with self._lock:
            if job_dir in self._seen:
                return
            if not is_job_ready(job_dir):
                return
            # mark seen early to avoid duplicate triggers
            self._seen.add(job_dir)

        # run processing in background thread
        t = threading.Thread(target=self._run_job, args=(job_dir,), daemon=True)
        t.start()

    def scan_incoming(self) -> None:
        """Periodic scan to catch jobs that existed before the watcher started."""
        now = time.time()
        # cheap throttle
        if now - self._last_scan < 1.0:
            return
        self._last_scan = now

        try:
            children = [p for p in self._cfg.incoming_dir.iterdir() if p.is_dir()]
        except Exception:
            return

        for job_dir in children:
            with self._lock:
                if job_dir in self._seen:
                    continue
                if not is_job_ready(job_dir):
                    continue
                self._seen.add(job_dir)

            t = threading.Thread(target=self._run_job, args=(job_dir,), daemon=True)
            t.start()

    def scan_processing(self) -> None:
        """Periodic scan to retry jobs that hit transient sync/lock errors."""
        try:
            children = [p for p in self._cfg.processing_dir.iterdir() if p.is_dir()]
        except Exception:
            return

        for job_dir in children:
            # Only retry jobs that explicitly asked for it.
            if not (job_dir / "RETRY_LATER.txt").exists():
                continue

            with self._lock:
                if job_dir in self._seen:
                    continue
                self._seen.add(job_dir)

            t = threading.Thread(target=self._run_job, args=(job_dir,), daemon=True)
            t.start()

    def _run_job(self, job_dir: Path) -> None:
        run_job_once(job_dir=job_dir, cfg=self._cfg, seen_set=self._seen, seen_lock=self._lock)


def run_job_once(*, job_dir: Path, cfg: WatchConfig, seen_set: set[Path] | None = None, seen_lock: threading.Lock | None = None) -> None:
    """Run a single job folder through processing -> done/failed.

    This is used by the watcher and can also be used by a desktop UI.
    """

    # Serialize processing by default to avoid collisions on shared outputs (deliveries/, zip tmp files)
    # and reduce Windows/Drive lock contention. Configure via WATCHER_MAX_CONCURRENCY.
    _JOB_SEM.acquire()
    try:
        _run_job_once_inner(job_dir=job_dir, cfg=cfg, seen_set=seen_set, seen_lock=seen_lock)
    finally:
        try:
            _JOB_SEM.release()
        except Exception:
            pass


def _run_job_once_inner(*, job_dir: Path, cfg: WatchConfig, seen_set: set[Path] | None = None, seen_lock: threading.Lock | None = None) -> None:
    job_name = job_dir.name
    processing = cfg.processing_dir / job_name
    done = cfg.done_dir / job_name
    failed = cfg.failed_dir / job_name

    incoming = cfg.incoming_dir / job_name

    try:
        processing.parent.mkdir(parents=True, exist_ok=True)
        job_dir.rename(processing)
    except Exception:
        processing = job_dir

    try:
        # Re-check readiness after move. Some sync providers momentarily dehydrate files during rename/move.
        if not is_job_ready(processing):
            # Treat this as transient so the watcher can retry once hydration completes.
            raise TransientJobError("job not ready after move (sync still in progress)")

        # If we’re retrying, clear stale markers so a success doesn’t leave scary artifacts behind.
        _clear_job_markers(processing)

        zip_path = process_job(job_dir=processing, deliveries_dir=cfg.deliveries_dir)
        (processing / "DELIVERED_ZIP.txt").write_text(str(zip_path), encoding="utf-8")

        def _move_to_done() -> None:
            done.parent.mkdir(parents=True, exist_ok=True)
            if done.exists():
                shutil.rmtree(done)
            processing.rename(done)

        try:
            with_retries(_move_to_done, attempts=_move_attempts())
        except TransientJobError as e:
            # If we can't rename the whole folder (common with Drive/OneDrive locks), still publish the deliverable
            # into done/ so downstream email automation can proceed.
            try:
                done.parent.mkdir(parents=True, exist_ok=True)
                done_fallback = done
                if done_fallback.exists():
                    done_fallback = cfg.done_dir / f"{job_name}__done_{int(time.time())}"
                done_fallback.mkdir(parents=True, exist_ok=True)

                # Best-effort: copy intake.yaml for traceability.
                try:
                    intake_src = processing / "intake.yaml"
                    if intake_src.exists():
                        shutil.copy2(intake_src, done_fallback / "intake.yaml")
                except Exception:
                    pass

                # Best-effort: copy contact email so the Drive emailer can send.
                try:
                    contact_src = processing / "CONTACT_EMAIL.txt"
                    if contact_src.exists():
                        shutil.copy2(contact_src, done_fallback / "CONTACT_EMAIL.txt")
                except Exception:
                    pass

                # Best-effort: copy delivery email template for operator visibility.
                try:
                    email_src = processing / "DELIVERY_EMAIL.txt"
                    if email_src.exists():
                        shutil.copy2(email_src, done_fallback / "DELIVERY_EMAIL.txt")
                except Exception:
                    pass

                # Ensure DELIVERABLE.zip exists in done.
                src_deliverable = processing / "DELIVERABLE.zip"
                try:
                    if src_deliverable.exists():
                        shutil.copy2(src_deliverable, done_fallback / "DELIVERABLE.zip")
                    else:
                        shutil.copy2(zip_path, done_fallback / "DELIVERABLE.zip")
                except Exception:
                    # If even copy fails, surface the transient error.
                    raise TransientJobError(f"move to done failed and could not copy deliverable: {e}")

                try:
                    (done_fallback / "DELIVERED_ZIP.txt").write_text(str(zip_path), encoding="utf-8")
                    (done_fallback / "DELIVERABLE_READY.txt").write_text(
                        "Deliverable created: DELIVERABLE.zip\n",
                        encoding="utf-8",
                    )
                except Exception:
                    pass

                # Leave the processing folder in place with RETRY_LATER.txt so we can clean up later.
                raise TransientJobError(
                    f"move to done failed (likely locked by sync): {e}. Deliverable copied to {done_fallback}."
                )
            except TransientJobError:
                raise
            except Exception:
                raise TransientJobError(f"move to done failed (likely locked by sync): {e}")

        # Hard guarantee for downstream Google Drive emailer:
        # Ensure the job folder itself contains `DELIVERABLE.zip`.
        # (Drive emailer scans EvidenceEngine/done/<job>/ for DELIVERABLE.zip.)
        try:
            deliverable = done / "DELIVERABLE.zip"
            if not deliverable.exists():
                for i in range(8):
                    try:
                        if deliverable.exists():
                            deliverable.unlink()
                        shutil.copy2(zip_path, deliverable)
                        (done / "DELIVERABLE_READY.txt").write_text(
                            "Deliverable created: DELIVERABLE.zip\n",
                            encoding="utf-8",
                        )
                        break
                    except OSError:
                        time.sleep(1.0 + (i * 0.25))
                    except Exception:
                        break
        except Exception:
            # Non-fatal: the pack ZIP still exists in deliveries/ and is referenced by DELIVERED_ZIP.txt.
            pass

        # Success path: ensure we do not leave ERROR/RETRY_LATER behind in done.
        _clear_job_markers(done)
    except TransientJobError as e:
        # If a deliverable already exists, treat the job as complete.
        # This handles cases where a later retry fails during cleanup (e.g., locked deliveries/ folder)
        # but a previous run already produced DELIVERABLE.zip.
        try:
            deliverable = processing / "DELIVERABLE.zip"
            if deliverable.exists() and deliverable.stat().st_size > 0:
                try:
                    done.parent.mkdir(parents=True, exist_ok=True)
                    if done.exists():
                        shutil.rmtree(done)
                    processing.rename(done)
                    _clear_job_markers(done)
                    return
                except Exception:
                    pass
        except Exception:
            pass

        try:
            (processing / "RETRY_LATER.txt").write_text(str(e), encoding="utf-8")
        except Exception:
            pass

        # Do NOT move back to incoming on transient errors.
        # Leaving the job in processing/ with RETRY_LATER.txt prevents a rapid duplicate-loop
        # (and avoids creating multiple job clones with suffixes).
        # If the folder is still under incoming (rename-to-processing failed earlier), try a best-effort
        # move into processing/ so scan_processing() can pick it up.
        try:
            if processing.parent != cfg.processing_dir:
                target = cfg.processing_dir / job_name
                if target.exists() and target != processing:
                    # A processing copy already exists (common after sync rename races).
                    # Merge any missing files from this folder into the existing target and quarantine
                    # this duplicate instead of spawning __retry clones.
                    _merge_dirs_best_effort(processing, target)
                    try:
                        qbase = _trash_dir(cfg) / f"dupes_{time.strftime('%Y%m%d_%H%M%S')}"
                        qbase.mkdir(parents=True, exist_ok=True)
                        processing.rename(qbase / processing.name)
                    except Exception:
                        pass
                    processing = target
                elif processing != target:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    processing.rename(target)
                    processing = target
        except Exception:
            pass

        if seen_set is not None and seen_lock is not None:
            with seen_lock:
                seen_set.discard(processing)
    except Exception as e:
        try:
            (processing / "ERROR.txt").write_text(str(e), encoding="utf-8")
        except Exception:
            pass
        try:
            failed.parent.mkdir(parents=True, exist_ok=True)
            if failed.exists():
                shutil.rmtree(failed)
            processing.rename(failed)
        except Exception:
            pass


def watch(cfg: WatchConfig, *, stop_event: threading.Event | None = None) -> None:
    cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
    cfg.processing_dir.mkdir(parents=True, exist_ok=True)
    cfg.done_dir.mkdir(parents=True, exist_ok=True)
    cfg.failed_dir.mkdir(parents=True, exist_ok=True)
    cfg.deliveries_dir.mkdir(parents=True, exist_ok=True)

    lock_path: Path | None = None
    try:
        lock_path = _acquire_watcher_lock(cfg)
    except WatcherAlreadyRunningError:
        raise
    except Exception as e:
        raise RuntimeError(f"Could not acquire watcher lock: {e}")

    handler = _Handler(cfg)
    observer = Observer()
    observer.schedule(handler, str(cfg.incoming_dir), recursive=True)
    observer.start()

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            handler.scan_incoming()
            handler.scan_processing()
            time.sleep(0.5)
    finally:
        observer.stop()
        observer.join()
        if lock_path is not None:
            try:
                lock_path.unlink(missing_ok=True)
            except Exception:
                pass
