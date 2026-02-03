from __future__ import annotations

import os
import re
import json
import base64
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import imaplib
import ssl
import socket
import urllib.error
import urllib.request
from datetime import datetime, timedelta, date
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Iterable

try:
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import filedialog, messagebox
    from tkinter import simpledialog
    from tkinter import ttk
except Exception as e:  # pragma: no cover
    raise RuntimeError("Tkinter is required for the desktop UI") from e

from .jobs import is_payment_required
from .envfile import default_env_file_path, load_default_env, save_env_file
from .marketing_rotation import generate_rotation, write_rotation_outputs
from .marketing_tracker import TrackedAd, load_tracker, make_key, save_tracker, try_fetch_views
from .watcher import WatchConfig, run_job_once, watch


APP_NAME = "EVIDEX"


def _env_truthy(name: str, default: str = "") -> bool:
    v = os.getenv(name, default)
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    s = str(raw).strip()
    if not s:
        return float(default)
    try:
        return float(s)
    except ValueError:
        return float(default)


def _default_watch_root() -> Path:
    # Priority: explicit env overrides
    env = (os.getenv("EVIDEX_WATCH_ROOT") or os.getenv("WATCH_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()

    def _looks_like_watch_root(p: Path) -> bool:
        try:
            if not p.exists() or not p.is_dir():
                return False
            required = ["incoming", "processing", "done", "failed", "deliveries"]
            return all((p / name).is_dir() for name in required)
        except Exception:
            return False

    # Common Drive-for-Desktop mirror locations (prefer these so the Drive emailer can see outputs).
    try:
        home = Path.home()
        desktop_candidates: list[Path] = [
            home / "Desktop",
            home / "OneDrive" / "Desktop",
        ]
        for base in desktop_candidates:
            for candidate in [base / "EvidenceEngine" / "EvidenceEngine", base / "EvidenceEngine"]:
                if _looks_like_watch_root(candidate):
                    return candidate.resolve()
    except Exception:
        pass

    # Workspace default
    try:
        repo_root = Path(__file__).resolve().parents[2]
    except Exception:
        repo_root = Path.cwd()
    return (repo_root / "_watch_root").resolve()


def _open_path(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        # Fallback
        subprocess.Popen(["explorer", str(path)])


def _open_url(url: str) -> None:
    u = (url or "").strip()
    if not u:
        return
    try:
        webbrowser.open(u)
    except Exception:
        pass


def _is_url(s: str) -> bool:
    x = (s or "").strip().lower()
    return x.startswith("http://") or x.startswith("https://")


def _google_form_url(form_id_or_url: str) -> str:
    v = (form_id_or_url or "").strip()
    if not v:
        return ""
    if _is_url(v):
        return v

    # Two common shapes:
    # - Public responder form ID: 1FAIpQL... (used in /forms/d/e/<id>/viewform)
    # - Editor form ID: a long "1..." ID (used in /forms/d/<id>/edit)
    if v.startswith("1FAIpQL"):
        return f"https://docs.google.com/forms/d/e/{v}/viewform"
    return f"https://docs.google.com/forms/d/{v}/edit"


def _google_drive_folder_url(folder_id_or_url: str) -> str:
    v = (folder_id_or_url or "").strip()
    if not v:
        return ""
    if _is_url(v):
        return v
    return f"https://drive.google.com/drive/folders/{v}"


def _extract_google_form_id(form_id_or_url: str) -> str:
    v = (form_id_or_url or "").strip()
    if not v:
        return ""
    if not _is_url(v):
        return v

    # Patterns:
    # - https://docs.google.com/forms/d/e/<id>/viewform
    # - https://docs.google.com/forms/d/<id>/edit
    m = re.search(r"/forms/d/e/([^/]+)/", v)
    if m:
        return m.group(1)
    m = re.search(r"/forms/d/([^/]+)/", v)
    if m:
        return m.group(1)
    return ""


def _extract_google_drive_folder_id(folder_id_or_url: str) -> str:
    v = (folder_id_or_url or "").strip()
    if not v:
        return ""
    if not _is_url(v):
        return v

    m = re.search(r"/drive/folders/([^/?#]+)", v)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([^&]+)", v)
    if m:
        return m.group(1)
    return ""


def _repo_root() -> Path:
    try:
        return Path(__file__).resolve().parents[2]
    except Exception:
        return Path.cwd()


def _format_ts(ts: float | None) -> str:
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        return ""


def _normalize_openai_base_url(base_url: str) -> str:
    b = (base_url or "").strip().rstrip("/")
    if not b:
        return ""

    # Ollama's OpenAI-compatible endpoints live under `/v1`.
    # Users often paste `http://localhost:11434` from the native API examples.
    low = b.lower()
    if low.endswith("/v1") or "/v1/" in low:
        return b
    if low.endswith(":11434"):
        return b + "/v1"

    return b


def _resolve_windows_cmd(cmd_base: str) -> str | None:
    """Best-effort resolution of Windows CLI tools for GUI-launched apps.

    Node/npm/clasp commonly install into locations that aren't always in PATH
    for already-running GUI processes.
    """

    base = (cmd_base or "").strip()
    if not base:
        return None

    # If it's already a path to an existing file, use it.
    try:
        p = Path(base)
        if p.exists() and p.is_file():
            return str(p)
    except Exception:
        pass

    # Try PATH resolution first.
    try:
        hit = shutil.which(base)
        if hit:
            return hit
    except Exception:
        hit = None

    # Typical Windows install locations.
    candidates: list[Path] = []
    appdata = os.getenv("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "npm" / f"{base}.cmd")
        candidates.append(Path(appdata) / "npm" / f"{base}.exe")

    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.getenv(env_name)
        if root:
            candidates.append(Path(root) / "nodejs" / f"{base}.cmd")
            candidates.append(Path(root) / "nodejs" / f"{base}.exe")

    for c in candidates:
        try:
            if c.exists() and c.is_file():
                return str(c)
        except Exception:
            continue

    return None


@dataclass(frozen=True)
class JobRow:
    name: str
    stage: str
    contact: str
    email_status: str
    paid: bool
    deliverable: bool
    emailed: bool
    uploads: int
    zip_mb: float
    flags: str
    updated: str
    path: Path


def _iter_job_dirs(root: Path) -> Iterable[tuple[str, Path]]:
    for stage in ("incoming", "processing", "done", "failed"):
        d = root / stage
        if not d.exists() or not d.is_dir():
            continue
        for child in d.iterdir():
            if child.is_dir():
                yield stage, child


def scan_jobs(root: Path) -> list[JobRow]:
    rows: list[JobRow] = []

    require_payment = is_payment_required()

    for stage, job_dir in _iter_job_dirs(root):
        paid = (job_dir / "PAID.txt").exists()
        deliverable = (job_dir / "DELIVERABLE.zip").exists()
        emailed = (job_dir / "SENT.txt").exists()

        # Contact (preview)
        contact = ""
        try:
            p = job_dir / "CONTACT_EMAIL.txt"
            if p.exists():
                contact = (p.read_text(encoding="utf-8", errors="replace").strip().splitlines() or [""])[0].strip()
        except Exception:
            contact = ""

        # Upload counts
        uploads = 0
        try:
            up = job_dir / "uploads"
            if up.exists() and up.is_dir():
                for p in up.rglob("*"):
                    if p.is_file():
                        name = p.name.lower()
                        if name in {"readme.txt", ".keep", ".gitkeep", "desktop.ini", "thumbs.db"}:
                            continue
                        uploads += 1
        except Exception:
            uploads = 0

        # Zip size
        zip_mb = 0.0
        try:
            z = job_dir / "DELIVERABLE.zip"
            if z.exists():
                zip_mb = float(z.stat().st_size) / (1024.0 * 1024.0)
        except Exception:
            zip_mb = 0.0

        flag_bits: list[str] = []
        if require_payment and not paid:
            flag_bits.append("waiting_payment")
        if (job_dir / "RETRY_LATER.txt").exists():
            flag_bits.append("retry_later")
        if (job_dir / "ERROR.txt").exists():
            flag_bits.append("error")
        if (job_dir / "UPLOADS_ACTION_REQUIRED.txt").exists():
            flag_bits.append("uploads_action_required")
        if (job_dir / "DELIVERABLE_READY.txt").exists() and not deliverable:
            flag_bits.append("deliverable_missing")

        # Email status (local view of markers; actual sending happens in Drive)
        has_contact = bool(contact)
        if emailed:
            email_status = "sent"
        elif not deliverable:
            email_status = "no_zip"
        elif require_payment and not paid:
            email_status = "blocked_payment"
        elif not has_contact:
            email_status = "no_contact"
        else:
            email_status = "ready"

        latest_mtime: float | None = None
        try:
            for p in job_dir.rglob("*"):
                try:
                    mt = p.stat().st_mtime
                except OSError:
                    continue
                if latest_mtime is None or mt > latest_mtime:
                    latest_mtime = mt
        except Exception:
            latest_mtime = None

        rows.append(
            JobRow(
                name=job_dir.name,
                stage=stage,
                contact=contact,
                email_status=email_status,
                paid=paid,
                deliverable=deliverable,
                emailed=emailed,
                uploads=uploads,
                zip_mb=zip_mb,
                flags=", ".join(flag_bits),
                updated=_format_ts(latest_mtime),
                path=job_dir,
            )
        )

    # Stable ordering
    rows.sort(key=lambda r: (r.stage, r.name))
    return rows


class _ScrollableFrame(ttk.Frame):
    """A vertical scroll container for long tab content."""

    def __init__(self, parent: tk.Misc):
        super().__init__(parent)

        self._canvas = tk.Canvas(self, highlightthickness=0)
        self._vbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vbar.set)

        self._vbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self.inner = ttk.Frame(self._canvas)
        self._window_id = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")

        def _on_inner_configure(_evt=None):
            try:
                self._canvas.configure(scrollregion=self._canvas.bbox("all"))
            except Exception:
                pass

        def _on_canvas_configure(evt):
            # Keep inner frame width equal to canvas width.
            try:
                self._canvas.itemconfigure(self._window_id, width=max(1, int(evt.width)))
            except Exception:
                pass

        self.inner.bind("<Configure>", _on_inner_configure)
        self._canvas.bind("<Configure>", _on_canvas_configure)

        # Mouse wheel scrolling (Windows)
        def _on_mousewheel(evt):
            try:
                delta = int(-1 * (evt.delta / 120))
                self._canvas.yview_scroll(delta, "units")
            except Exception:
                pass

        def _bind_wheel(_evt=None):
            try:
                self._canvas.bind_all("<MouseWheel>", _on_mousewheel)
            except Exception:
                pass

        def _unbind_wheel(_evt=None):
            try:
                self._canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass

        self.inner.bind("<Enter>", _bind_wheel)
        self.inner.bind("<Leave>", _unbind_wheel)


class EvidexDesktop(tk.Tk):
    def __init__(self) -> None:
        # Load persisted settings early so StringVar defaults see them.
        self._env_path = load_default_env(override=False)

        super().__init__()
        self.title("EVIDEX — Evidence Pack Engine")
        self.geometry("1200x720")

        try:
            style = ttk.Style(self)
            if "clam" in style.theme_names():
                style.theme_use("clam")
            style.configure("Header.TLabel", font=("Segoe UI", 18, "bold"))
            style.configure("Subtle.TLabel", foreground="#5a6675")
            style.configure("Accent.TButton", foreground="#ffffff")
            style.map("Accent.TButton", background=[("active", "#0b5bd3")])
        except Exception:
            pass

        self._watch_thread: threading.Thread | None = None
        self._watch_stop = threading.Event()

        self._root_var = tk.StringVar(value=str(_default_watch_root()))
        self._auto_refresh_var = tk.BooleanVar(value=True)
        self._refine_ads_var = tk.BooleanVar(value=True)
        self._images_ads_var = tk.BooleanVar(value=False)

        self._payment_link_var = tk.StringVar(value=(os.getenv("PAYMENT_LINK") or "").strip())
        self._paypal_link_var = tk.StringVar(value=(os.getenv("PAYPAL_LINK") or "").strip())

        # Optional Google intake pointers (for operator convenience)
        self._google_form_id_var = tk.StringVar(value=(os.getenv("EVIDEX_GOOGLE_FORM_ID") or "").strip())
        self._google_drive_folder_id_var = tk.StringVar(value=(os.getenv("EVIDEX_GOOGLE_DRIVE_FOLDER_ID") or "").strip())
        self._google_remember_var = tk.BooleanVar(value=False)

        # Optional Google Apps Script (clasp) convenience
        self._clasp_cmd_var = tk.StringVar(value=(os.getenv("EVIDEX_CLASP_CMD") or "clasp").strip() or "clasp")
        self._gas_project_dir_var = tk.StringVar(value=(os.getenv("EVIDEX_GAS_PROJECT_DIR") or "").strip())
        self._gas_status_var = tk.StringVar(value="Apps Script: (not configured)")

        # Optional inbox monitoring (Gmail IMAP)
        # Default to the operator mailbox; do not hardcode passwords.
        self._imap_user_var = tk.StringVar(value=(os.getenv("EVIDEX_EMAIL_IMAP_USER") or "buntbyron@gmail.com").strip())
        self._imap_pass_var = tk.StringVar(value=(os.getenv("EVIDEX_EMAIL_IMAP_PASSWORD") or "").strip())
        self._imap_host_var = tk.StringVar(value=(os.getenv("EVIDEX_EMAIL_IMAP_HOST") or "imap.gmail.com").strip())
        self._imap_port_var = tk.StringVar(value=(os.getenv("EVIDEX_EMAIL_IMAP_PORT") or "993").strip())
        self._imap_remember_var = tk.BooleanVar(value=False)
        self._imap_unread_var = tk.StringVar(value="Unread: (not configured)")
        self._imap_status_var = tk.StringVar(value="Inbox: not connected")
        self._imap_stop = threading.Event()
        self._imap_refresh = threading.Event()

        # Operational toggles (apply immediately for this process)
        self._require_payment_var = tk.BooleanVar(value=str(os.getenv("REQUIRE_PAYMENT", "0")).strip().lower() in {"1", "true", "yes", "y", "on"})
        self._ai_enabled_var = tk.BooleanVar(value=not (str(os.getenv("LLM_DISABLED", "0")).strip().lower() in {"1", "true", "yes", "y", "on"}))
        self._summary_ai_var = tk.BooleanVar(value=str(os.getenv("SUMMARY_USE_LLM", "1")).strip().lower() in {"1", "true", "yes", "y", "on"})
        self._narrative_ai_var = tk.BooleanVar(value=str(os.getenv("NARRATIVE_USE_LLM", "0")).strip().lower() in {"1", "true", "yes", "y", "on"})

        # Ollama / OpenAI-compatible endpoint settings
        default_base = (os.getenv("OLLAMA_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "http://localhost:11434/v1").strip()
        default_model = (os.getenv("OLLAMA_MODEL") or os.getenv("OPENAI_MODEL") or "llama3.2:3b").strip()
        self._ollama_base_url_var = tk.StringVar(value=default_base)
        self._ollama_model_var = tk.StringVar(value=default_model)
        self._ollama_model_summary_var = tk.StringVar(value=(os.getenv("OLLAMA_MODEL_SUMMARY") or os.getenv("OPENAI_MODEL_SUMMARY") or "").strip())
        self._ollama_model_narrative_var = tk.StringVar(value=(os.getenv("OLLAMA_MODEL_NARRATIVE") or os.getenv("OPENAI_MODEL_NARRATIVE") or "").strip())
        self._ollama_exe_var = tk.StringVar(value=(os.getenv("OLLAMA_EXE_PATH") or "ollama").strip() or "ollama")
        self._ollama_status_var = tk.StringVar(value="LLM endpoint: (not checked)")
        self._ollama_proc: subprocess.Popen | None = None

        self._build_ui()
        self._apply_ui_settings_to_env()
        self._refresh_jobs()
        self._refresh_dashboard()
        self.after(1000, self._auto_refresh_tick)
        self.after(1500, self._dashboard_tick)
        self._start_imap_monitor()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        header = tk.Frame(self, bg="#0f2440")
        header.pack(fill="x")

        self._logo_img = self._try_load_logo(target_width=84)
        if self._logo_img is not None:
            tk.Label(header, image=self._logo_img, bg="#0f2440").pack(side="left", padx=(14, 10), pady=12)
            try:
                self.iconphoto(True, self._logo_img)
            except Exception:
                pass

        title_box = tk.Frame(header, bg="#0f2440")
        title_box.pack(side="left", fill="y", pady=10)
        tk.Label(title_box, text="EVIDEX", fg="#ffffff", bg="#0f2440", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        tk.Label(title_box, text="Evidence packs with human QA + automation", fg="#a9c2e6", bg="#0f2440", font=("Segoe UI", 10)).pack(anchor="w")

        right_box = tk.Frame(header, bg="#0f2440")
        right_box.pack(side="right", fill="y", padx=14, pady=10)
        self._watch_status = tk.StringVar(value="Watcher: stopped")
        tk.Label(right_box, textvariable=self._watch_status, fg="#ffffff", bg="#0f2440", font=("Segoe UI", 11, "bold")).pack(anchor="e")
        tk.Label(right_box, textvariable=self._imap_unread_var, fg="#a9c2e6", bg="#0f2440", font=("Segoe UI", 10)).pack(anchor="e")

        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(top, text="Watch root:").pack(side="left")
        ttk.Entry(top, textvariable=self._root_var, width=78).pack(side="left", padx=8)
        ttk.Button(top, text="Browse…", command=self._browse_root).pack(side="left")
        ttk.Button(top, text="Open", command=self._open_root).pack(side="left", padx=6)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._jobs_tab = ttk.Frame(nb)
        self._dashboard_tab = ttk.Frame(nb)
        self._watcher_tab = ttk.Frame(nb)
        self._google_tab = ttk.Frame(nb)
        self._marketing_tab = ttk.Frame(nb)

        nb.add(self._dashboard_tab, text="Dashboard")
        nb.add(self._jobs_tab, text="Jobs")
        nb.add(self._watcher_tab, text="Watcher")
        nb.add(self._google_tab, text="Google")
        nb.add(self._marketing_tab, text="Marketing")

        self._build_dashboard_tab(self._dashboard_tab)
        self._build_jobs_tab(self._jobs_tab)
        self._build_watcher_tab(self._watcher_tab)
        self._build_google_tab(self._google_tab)
        self._build_marketing_tab(self._marketing_tab)

    def _build_google_tab(self, parent: ttk.Frame) -> None:
        # Google setup can get tall; use a scroll container.
        scroll = _ScrollableFrame(parent)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner

        intro = (
            "This tab helps you set up the Forms → Drive → Local Mirror automation.\n"
            "Tip: Node.js + clasp are required for the automation buttons."
        )
        ttk.Label(body, text=intro, foreground="#555", justify="left").pack(fill="x", padx=12, pady=(12, 8))

        ids = ttk.LabelFrame(body, text="Google IDs")
        ids.pack(fill="x", padx=10, pady=(0, 10))

        row_ids = ttk.Frame(ids)
        row_ids.pack(fill="x", padx=10, pady=10)
        ttk.Label(row_ids, text="Google Form ID/URL:").pack(side="left")
        ttk.Entry(row_ids, textvariable=self._google_form_id_var, width=52).pack(side="left", padx=6)
        ttk.Label(row_ids, text="Drive folder ID/URL:").pack(side="left", padx=(10, 0))
        ttk.Entry(row_ids, textvariable=self._google_drive_folder_id_var, width=52).pack(side="left", padx=6)
        ttk.Checkbutton(row_ids, text="Remember", variable=self._google_remember_var).pack(side="left", padx=8)

        row_ids_btn = ttk.Frame(ids)
        row_ids_btn.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(row_ids_btn, text="Save Google IDs", command=self._save_google_settings).pack(side="left")
        ttk.Button(row_ids_btn, text="Open form", command=self._open_google_form).pack(side="left", padx=8)
        ttk.Button(row_ids_btn, text="Open Drive folder", command=self._open_google_drive_folder).pack(side="left")

        mirror = ttk.LabelFrame(body, text="Drive for Desktop mirroring")
        mirror.pack(fill="x", padx=10, pady=(0, 10))

        row_m1 = ttk.Frame(mirror)
        row_m1.pack(fill="x", padx=10, pady=10)
        ttk.Button(row_m1, text="Drive for Desktop download", command=lambda: _open_url("https://www.google.com/drive/download/")).pack(side="left")
        ttk.Button(row_m1, text="Verify mirror", command=self._verify_drive_mirror).pack(side="left", padx=8)
        ttk.Button(row_m1, text="Run setup checklist", command=self._google_setup_checklist, style="Accent.TButton").pack(side="left", padx=8)
        ttk.Button(row_m1, text="Open setup guide", command=self._open_form_to_drive_setup_guide).pack(side="left")

        ttk.Label(mirror, textvariable=self._gas_status_var, foreground="#555").pack(fill="x", padx=10, pady=(0, 10))

        tools = ttk.LabelFrame(body, text="Node.js + clasp tools")
        tools.pack(fill="x", padx=10, pady=(0, 10))

        row_t1 = ttk.Frame(tools)
        row_t1.pack(fill="x", padx=10, pady=10)
        ttk.Button(row_t1, text="Download Node.js (LTS)", command=lambda: _open_url("https://nodejs.org/en/download")).pack(side="left")
        ttk.Button(row_t1, text="Check node/npm/clasp", command=self._tools_check_node_clasp).pack(side="left", padx=8)
        ttk.Button(row_t1, text="Install clasp (@google/clasp)", command=self._tools_install_clasp).pack(side="left")
        ttk.Button(row_t1, text="clasp login", command=self._tools_clasp_login).pack(side="left", padx=8)
        ttk.Button(row_t1, text="clasp login (OAuth JSON…)", command=self._tools_clasp_login_with_creds).pack(side="left")

        gas = ttk.LabelFrame(body, text="Google Apps Script automation (clasp)")
        gas.pack(fill="x", padx=10, pady=(0, 12))

        gas_row1 = ttk.Frame(gas)
        gas_row1.pack(fill="x", padx=10, pady=10)
        ttk.Label(gas_row1, text="clasp cmd:").pack(side="left")
        ttk.Entry(gas_row1, textvariable=self._clasp_cmd_var, width=18).pack(side="left", padx=6)
        ttk.Label(gas_row1, text="Project dir:").pack(side="left", padx=(10, 0))
        ttk.Entry(gas_row1, textvariable=self._gas_project_dir_var, width=66).pack(side="left", padx=6)
        ttk.Button(gas_row1, text="Browse…", command=self._browse_gas_project_dir).pack(side="left")

        gas_row2 = ttk.Frame(gas)
        gas_row2.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(gas_row2, text="Save GAS settings", command=self._save_gas_settings).pack(side="left")
        ttk.Button(gas_row2, text="clasp account", command=self._gas_login_status).pack(side="left", padx=8)
        ttk.Button(gas_row2, text="Init clasp project…", command=self._gas_init_wizard).pack(side="left")
        ttk.Button(gas_row2, text="Push code", command=self._gas_push).pack(side="left", padx=8)
        ttk.Button(gas_row2, text="Open editor", command=self._gas_open_editor).pack(side="left")

        gas_row3 = ttk.Frame(gas)
        gas_row3.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(gas_row3, text="Create intake form", command=self._gas_create_intake_form).pack(side="left")
        ttk.Button(gas_row3, text="Configure IDs", command=self._gas_configure_ids).pack(side="left", padx=8)
        ttk.Button(gas_row3, text="Run setupTrigger()", command=lambda: self._gas_run("setupTrigger")).pack(side="left")
        ttk.Button(gas_row3, text="Run setupDeliveryTrigger()", command=lambda: self._gas_run("setupDeliveryTrigger")).pack(side="left", padx=8)

        gas_hint = (
            "Notes:\n"
            "- The Google account used is whichever account you authenticated with via `clasp login`.\n"
            "- If you used the wrong account: run `clasp logout` then `clasp login` again."
        )
        ttk.Label(gas, text=gas_hint, foreground="#555", justify="left").pack(fill="x", padx=10, pady=(0, 10))

    def _build_dashboard_tab(self, parent: ttk.Frame) -> None:
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        top = ttk.Frame(outer)
        top.pack(fill="x")

        self._dash_window_var = tk.StringVar(value="7d")
        ttk.Label(top, text="Window:").pack(side="left")
        ttk.OptionMenu(top, self._dash_window_var, "7d", "1d", "7d", "30d", command=lambda _v: self._refresh_dashboard()).pack(side="left", padx=6)
        ttk.Button(top, text="Refresh", command=self._refresh_dashboard).pack(side="left", padx=6)
        ttk.Button(top, text="Open Gmail inbox", command=self._open_gmail_inbox).pack(side="right")

        cards = ttk.Frame(outer)
        cards.pack(fill="x", pady=(10, 8))

        self._dash_summary_var = tk.StringVar(value="")
        ttk.Label(cards, textvariable=self._dash_summary_var, style="Subtle.TLabel", justify="left").pack(side="left", fill="x", expand=True)

        inbox = ttk.LabelFrame(outer, text="Inbox monitor (optional)")
        inbox.pack(fill="x", pady=(0, 10))

        row = ttk.Frame(inbox)
        row.pack(fill="x", padx=10, pady=8)
        ttk.Label(row, text="IMAP host:").pack(side="left")
        ttk.Entry(row, textvariable=self._imap_host_var, width=22).pack(side="left", padx=6)
        ttk.Label(row, text="Port:").pack(side="left")
        ttk.Entry(row, textvariable=self._imap_port_var, width=6).pack(side="left", padx=6)
        ttk.Label(row, text="User:").pack(side="left", padx=(10, 0))
        ttk.Entry(row, textvariable=self._imap_user_var, width=26).pack(side="left", padx=6)
        ttk.Label(row, text="App password:").pack(side="left", padx=(10, 0))
        ttk.Entry(row, textvariable=self._imap_pass_var, width=24, show="*").pack(side="left", padx=6)
        ttk.Checkbutton(row, text="Remember", variable=self._imap_remember_var).pack(side="left", padx=8)
        ttk.Button(row, text="Connect", command=self._kick_imap_refresh).pack(side="left")

        row2 = ttk.Frame(inbox)
        row2.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(row2, textvariable=self._imap_status_var, style="Subtle.TLabel").pack(side="left")

        charts = ttk.Frame(outer)
        charts.pack(fill="both", expand=True)

        left = ttk.LabelFrame(charts, text="Requests")
        right = ttk.LabelFrame(charts, text="Payments / revenue")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right.pack(side="left", fill="both", expand=True)

        self._chart_requests = tk.Canvas(left, height=260, background="#ffffff", highlightthickness=1, highlightbackground="#d6dde6")
        self._chart_requests.pack(fill="both", expand=True, padx=10, pady=10)
        self._chart_requests.bind("<Configure>", lambda _e: self.after(150, self._refresh_dashboard))

        self._chart_payments = tk.Canvas(right, height=260, background="#ffffff", highlightthickness=1, highlightbackground="#d6dde6")
        self._chart_payments.pack(fill="both", expand=True, padx=10, pady=10)
        self._chart_payments.bind("<Configure>", lambda _e: self.after(150, self._refresh_dashboard))

    def _build_jobs_tab(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", pady=(10, 6), padx=10)

        ttk.Button(toolbar, text="Refresh", command=self._refresh_jobs).pack(side="left")
        ttk.Checkbutton(toolbar, text="Auto refresh", variable=self._auto_refresh_var).pack(side="left", padx=10)
        ttk.Button(toolbar, text="Cleanup tests/duplicates", command=self._cleanup_jobs_trash).pack(side="left", padx=10)

        # Bottom action bar: pack it *before* the expanding table/details split so it always
        # retains enough vertical space to render button text.
        hint = (
            "Note: ‘Emailed’ reflects SENT.txt if present (typically written by the Drive Apps Script emailer). "
            "Local processing does not send emails by itself."
        )
        ttk.Label(parent, text=hint, foreground="#555").pack(fill="x", padx=10, pady=(0, 10), side="bottom")

        btns = ttk.Frame(parent)
        btns.pack(fill="x", padx=10, pady=(6, 10), side="bottom")

        ttk.Button(btns, text="Open job folder", command=self._open_selected_job).pack(side="left", padx=(0, 8), pady=2)
        ttk.Button(btns, text="Open deliverable ZIP", command=self._open_selected_zip).pack(side="left", padx=(0, 8), pady=2)
        ttk.Button(btns, text="Mark paid (PAID.txt)", command=self._mark_selected_paid).pack(side="left", padx=(0, 8), pady=2)
        ttk.Button(btns, text="Open PayPal/payment link", command=self._open_payment_link).pack(side="left", padx=(0, 8), pady=2)
        ttk.Button(btns, text="Process now", command=self._process_selected_now).pack(side="left", padx=(0, 8), pady=2)

        cols = ("job", "stage", "contact", "email", "paid", "deliverable", "sent", "uploads", "zip_mb", "updated", "flags")
        split = ttk.Panedwindow(parent, orient="vertical")
        split.pack(fill="both", expand=True, padx=10, side="top")

        table_box = ttk.Frame(split)
        details_box = ttk.Frame(split)
        split.add(table_box, weight=3)
        split.add(details_box, weight=1)

        tree_wrap = ttk.Frame(table_box)
        tree_wrap.pack(fill="both", expand=True)

        self._tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", height=16)
        ybar = ttk.Scrollbar(tree_wrap, orient="vertical", command=self._tree.yview)
        xbar = ttk.Scrollbar(tree_wrap, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)

        ybar.pack(side="right", fill="y")
        xbar.pack(side="bottom", fill="x")
        self._tree.pack(side="left", fill="both", expand=True)
        self._tree.bind("<<TreeviewSelect>>", lambda _e: self._update_selected_details())

        self._tree.heading("job", text="Job")
        self._tree.heading("stage", text="Stage")
        self._tree.heading("contact", text="Contact")
        self._tree.heading("email", text="Email")
        self._tree.heading("paid", text="Paid")
        self._tree.heading("deliverable", text="Deliverable")
        self._tree.heading("sent", text="Sent")
        self._tree.heading("uploads", text="Uploads")
        self._tree.heading("zip_mb", text="ZIP (MB)")
        self._tree.heading("flags", text="Flags")
        self._tree.heading("updated", text="Updated")

        self._tree.column("job", width=260, anchor="w")
        self._tree.column("stage", width=95, anchor="w")
        self._tree.column("contact", width=240, anchor="w")
        self._tree.column("email", width=120, anchor="w")
        self._tree.column("paid", width=60, anchor="center")
        self._tree.column("deliverable", width=85, anchor="center")
        self._tree.column("sent", width=60, anchor="center")
        self._tree.column("uploads", width=70, anchor="e")
        self._tree.column("zip_mb", width=75, anchor="e")
        self._tree.column("updated", width=170, anchor="w")
        self._tree.column("flags", width=520, anchor="w")

        # Row coloring (Treeview tags)
        try:
            self._tree.tag_configure("failed", background="#ffe8e8")
            self._tree.tag_configure("error", background="#ffe0e0")
            self._tree.tag_configure("retry", background="#fff1d6")
            self._tree.tag_configure("ready", background="#dff7ff")
            self._tree.tag_configure("done", background="#e8fff0")
            self._tree.tag_configure("processing", background="#fff6d6")
            self._tree.tag_configure("incoming", background="#e8f1ff")
            self._tree.tag_configure("blocked", background="#f3f3f3")
        except Exception:
            pass

        # Details pane
        details_label = ttk.Label(details_box, text="Selected job details:")
        details_label.pack(anchor="w", padx=0, pady=(6, 2))
        self._details = tk.Text(details_box, height=8)
        self._details.pack(fill="both", expand=True)
        self._details.insert("end", "Select a job row to see details.\n")
        self._details.configure(state="disabled")

    def _build_watcher_tab(self, parent: ttk.Frame) -> None:
        # The Watcher tab is the "operations" hub and can get tall; make it scroll.
        scroll = _ScrollableFrame(parent)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner

        box = ttk.LabelFrame(body, text="Watcher controls")
        box.pack(fill="x", padx=10, pady=(12, 8))

        row = ttk.Frame(box)
        row.pack(fill="x", padx=10, pady=10)
        ttk.Button(row, text="Start watcher", command=self._start_watcher, style="Accent.TButton").pack(side="left")
        ttk.Button(row, text="Stop watcher", command=self._stop_watcher).pack(side="left", padx=8)
        ttk.Button(row, text="Process selected now", command=self._process_selected_now).pack(side="left", padx=8)

        self._watcher_note = tk.StringVar(value="")
        ttk.Label(box, textvariable=self._watcher_note, foreground="#555", justify="left").pack(fill="x", padx=10, pady=(0, 10))

        quick = ttk.LabelFrame(body, text="Google quick links")
        quick.pack(fill="x", padx=10, pady=(0, 10))

        q1 = ttk.Frame(quick)
        q1.pack(fill="x", padx=10, pady=10)
        ttk.Button(q1, text="Open Google tab", command=lambda: self._switch_to_google_tab()).pack(side="left")
        ttk.Button(q1, text="Open Drive folder", command=self._open_google_drive_folder).pack(side="left", padx=8)
        ttk.Button(q1, text="Open form", command=self._open_google_form).pack(side="left")
        ttk.Label(quick, textvariable=self._gas_status_var, foreground="#555").pack(fill="x", padx=10, pady=(0, 10))

        settings = ttk.LabelFrame(body, text="Operator settings (applies immediately)")
        settings.pack(fill="x", padx=10, pady=(0, 10))

        row1 = ttk.Frame(settings)
        row1.pack(fill="x", padx=10, pady=8)
        ttk.Checkbutton(row1, text="Require payment before processing", variable=self._require_payment_var, command=self._apply_require_payment).pack(side="left")

        row_pay = ttk.Frame(settings)
        row_pay.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(row_pay, text="Payment link:").pack(side="left")
        ttk.Entry(row_pay, textvariable=self._payment_link_var, width=58).pack(side="left", padx=6)
        ttk.Label(row_pay, text="PayPal link:").pack(side="left", padx=(10, 0))
        ttk.Entry(row_pay, textvariable=self._paypal_link_var, width=58).pack(side="left", padx=6)

        row_pay_btn = ttk.Frame(settings)
        row_pay_btn.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(row_pay_btn, text="Save links", command=self._save_payment_links).pack(side="left")
        ttk.Button(row_pay_btn, text="Open settings file", command=self._open_settings_file).pack(side="left", padx=8)

        # Google IDs + clasp controls live in the Google tab (to keep this tab focused).

        row2 = ttk.Frame(settings)
        row2.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Checkbutton(row2, text="Enable AI (Ollama/OpenAI)", variable=self._ai_enabled_var, command=self._apply_ai_enable).pack(side="left")
        ttk.Checkbutton(row2, text="AI for KPI summaries", variable=self._summary_ai_var, command=self._apply_ai_modes).pack(side="left", padx=10)
        ttk.Checkbutton(row2, text="AI for narrative", variable=self._narrative_ai_var, command=self._apply_ai_modes).pack(side="left")

        row3 = ttk.Frame(settings)
        row3.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Label(row3, text="Endpoint (OLLAMA_BASE_URL):").pack(side="left")
        ttk.Entry(row3, textvariable=self._ollama_base_url_var, width=46).pack(side="left", padx=6)
        ttk.Label(row3, text="Model (OLLAMA_MODEL):").pack(side="left", padx=(10, 0))
        ttk.Entry(row3, textvariable=self._ollama_model_var, width=22).pack(side="left", padx=6)

        row4 = ttk.Frame(settings)
        row4.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(row4, text="Summary model (optional):").pack(side="left")
        ttk.Entry(row4, textvariable=self._ollama_model_summary_var, width=24).pack(side="left", padx=6)
        ttk.Label(row4, text="Narrative model (optional):").pack(side="left", padx=(10, 0))
        ttk.Entry(row4, textvariable=self._ollama_model_narrative_var, width=24).pack(side="left", padx=6)

        row5 = ttk.Frame(settings)
        row5.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(row5, text="Ollama exe:").pack(side="left")
        ttk.Entry(row5, textvariable=self._ollama_exe_var, width=32).pack(side="left", padx=6)
        ttk.Button(row5, text="Browse…", command=self._browse_ollama_exe).pack(side="left")

        row6 = ttk.Frame(settings)
        row6.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(row6, text="Save AI settings", command=self._save_ai_settings).pack(side="left")
        ttk.Button(row6, text="Check endpoint", command=self._check_llm_endpoint).pack(side="left", padx=8)
        ttk.Button(row6, text="Start Ollama", command=self._start_ollama_server).pack(side="left")
        ttk.Button(row6, text="Pull model", command=self._pull_ollama_model).pack(side="left", padx=8)
        ttk.Button(row6, text="Test completion", command=self._test_llm_completion).pack(side="left")
        ttk.Button(row6, text="Ollama download", command=lambda: _open_url("https://ollama.com/download")).pack(side="left", padx=8)

        ttk.Label(settings, textvariable=self._ollama_status_var, foreground="#555").pack(fill="x", padx=10, pady=(0, 6))

        llm_hint = (
            "Tip: Enable AI requires a configured endpoint (e.g. OLLAMA_BASE_URL + OLLAMA_MODEL). "
            "If disabled, the engine uses deterministic fallbacks and still produces complete packs."
        )
        ttk.Label(settings, text=llm_hint, foreground="#555").pack(fill="x", padx=10, pady=(0, 10))

        tips = (
            "Tips:\n"
            "- Watcher watches incoming/ and automatically moves jobs through processing/ → done/ or failed/\n"
            "- If REQUIRE_PAYMENT=1 is set, jobs will wait until PAID.txt exists in the job folder\n"
            "- For Google automation and mirroring, use the Google tab"
        )
        ttk.Label(body, text=tips, justify="left", foreground="#555").pack(fill="x", padx=14, pady=(0, 14))

    def _switch_to_google_tab(self) -> None:
        try:
            # Find the nearest notebook and select our google tab.
            nb = self._google_tab.master
            if isinstance(nb, ttk.Notebook):
                nb.select(self._google_tab)
        except Exception:
            pass

    def _tools_check_node_clasp(self) -> None:
        """Check if node/npm/clasp are available and show versions."""
        self._gas_status_var.set("Tools: checking node/npm/clasp…")

        def _run() -> None:
            def _try(cmd: list[str]) -> tuple[bool, str]:
                try:
                    out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                    text = ((out.stdout or "") + "\n" + (out.stderr or "")).strip()
                    if out.returncode == 0:
                        return True, (text.splitlines()[0] if text else "ok")
                    return False, (text or f"exit {out.returncode}")
                except FileNotFoundError:
                    return False, "not found"
                except Exception as e:
                    return False, f"{type(e).__name__}: {e}"

            node_cmd = _resolve_windows_cmd("node") or "node"
            npm_cmd = _resolve_windows_cmd("npm") or "npm"
            clasp_cmd = self._gas_cmd()

            ok_node, v_node = _try([node_cmd, "--version"])
            ok_npm, v_npm = _try([npm_cmd, "--version"])
            ok_clasp, v_clasp = _try([clasp_cmd, "--version"])

            if ok_clasp:
                # Update clasp cmd field for this session if we found a concrete path.
                try:
                    current = (self._clasp_cmd_var.get() or "").strip()
                    if clasp_cmd and current != clasp_cmd:
                        self.after(0, lambda c=clasp_cmd: self._clasp_cmd_var.set(c))
                except Exception:
                    pass

            msg = (
                f"node: {'OK' if ok_node else 'MISSING'} ({v_node})\n"
                f"npm: {'OK' if ok_npm else 'MISSING'} ({v_npm})\n"
                f"clasp: {'OK' if ok_clasp else 'MISSING'} ({v_clasp})\n"
                "\nResolved commands (what EVIDEX is using):\n"
                f"- node: {node_cmd}\n"
                f"- npm: {npm_cmd}\n"
                f"- clasp: {clasp_cmd}\n"
                "\nIf these show missing but work in PowerShell, restart EVIDEX (it may have launched before PATH updated).\n"
            )
            self.after(0, lambda: self._gas_status_var.set("Tools: check complete"))
            self.after(0, lambda m=msg: messagebox.showinfo("Tool check", m))

        threading.Thread(target=_run, daemon=True).start()

    def _tools_install_clasp(self) -> None:
        """Install @google/clasp globally via npm."""
        if not messagebox.askyesno(
            "Install clasp?",
            "This will run: npm i -g @google/clasp\n\n"
            "It may require admin permissions depending on your Node.js setup.\n\nContinue?",
        ):
            return

        self._gas_status_var.set("Tools: installing clasp…")

        def _run() -> None:
            try:
                npm_cmd = _resolve_windows_cmd("npm") or "npm"
                out = subprocess.run([npm_cmd, "i", "-g", "@google/clasp"], capture_output=True, text=True, timeout=60 * 8)
                text = ((out.stdout or "") + "\n" + (out.stderr or "")).strip()
                if out.returncode == 0:
                    self.after(0, lambda: self._gas_status_var.set("Tools: clasp installed"))
                    self.after(0, lambda: messagebox.showinfo("Install clasp", text[-1600:] or "Installed."))
                else:
                    self.after(0, lambda: self._gas_status_var.set("Tools: clasp install failed"))
                    self.after(0, lambda t=text: messagebox.showwarning("Install clasp", t[-1600:] or "Install failed."))
            except FileNotFoundError:
                self.after(0, lambda: self._gas_status_var.set("Tools: npm not found (install Node.js)"))
                self.after(0, lambda: messagebox.showinfo("Install clasp", "npm not found. Install Node.js (LTS) first."))
            except Exception as e:
                self.after(0, lambda: self._gas_status_var.set("Tools: clasp install error"))
                self.after(0, lambda: messagebox.showerror("Install clasp", f"Install failed: {e}"))

        threading.Thread(target=_run, daemon=True).start()

    def _tools_clasp_login(self) -> None:
        """Run `clasp login` to authenticate in the browser."""
        if not messagebox.askyesno(
            "clasp login",
            "This will run `clasp login` and open a browser window to authenticate.\n\n"
            "Use your preferred Gmail account.\n\nContinue?",
        ):
            return

        self._gas_status_var.set("Tools: running clasp login…")

        def _run() -> None:
            try:
                out = subprocess.run([self._gas_cmd(), "login"], capture_output=True, text=True, timeout=60 * 10)
                text = ((out.stdout or "") + "\n" + (out.stderr or "")).strip()
                if out.returncode == 0:
                    self.after(0, lambda: self._gas_status_var.set("Tools: clasp login complete"))
                    self.after(0, lambda t=text: messagebox.showinfo("clasp login", t[-1600:] or "Login complete."))
                else:
                    self.after(0, lambda: self._gas_status_var.set("Tools: clasp login failed"))
                    self.after(0, lambda t=text: messagebox.showwarning("clasp login", t[-1600:] or "Login failed."))
            except FileNotFoundError:
                self.after(0, lambda: self._gas_status_var.set("Tools: clasp not found"))
                self.after(0, lambda: messagebox.showinfo("clasp login", "clasp not found. Install @google/clasp first."))
            except Exception as e:
                self.after(0, lambda: self._gas_status_var.set("Tools: clasp login error"))
                self.after(0, lambda: messagebox.showerror("clasp login", f"Login failed: {e}"))

        threading.Thread(target=_run, daemon=True).start()

    def _tools_clasp_login_with_creds(self) -> None:
        """Run `clasp login --creds <json>` using a user-provided OAuth client JSON.

        Useful when the default clasp OAuth client is blocked or you want to use your own Google Cloud project.
        """

        creds_path = filedialog.askopenfilename(
            title="Select OAuth client JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not creds_path:
            return

        if not messagebox.askyesno(
            "clasp login (custom OAuth)",
            "This will run `clasp login --creds <file>` and open a browser window to authenticate.\n\n"
            "Note: This does NOT remove the need to enable the Apps Script API; it only changes which OAuth client is used.\n\n"
            "Continue?",
        ):
            return

        self._gas_status_var.set("Tools: running clasp login (custom OAuth)…")

        def _run() -> None:
            try:
                out = subprocess.run(
                    [self._gas_cmd(), "login", "--creds", creds_path],
                    capture_output=True,
                    text=True,
                    timeout=60 * 10,
                )
                text = ((out.stdout or "") + "\n" + (out.stderr or "")).strip()
                if out.returncode == 0:
                    self.after(0, lambda: self._gas_status_var.set("Tools: clasp login complete"))
                    self.after(0, lambda t=text: messagebox.showinfo("clasp login", t[-1600:] or "Login complete."))
                else:
                    self.after(0, lambda: self._gas_status_var.set("Tools: clasp login failed"))
                    self.after(0, lambda t=text: messagebox.showwarning("clasp login", t[-1600:] or "Login failed."))
            except FileNotFoundError:
                self.after(0, lambda: self._gas_status_var.set("Tools: clasp not found"))
                self.after(0, lambda: messagebox.showinfo("clasp login", "clasp not found. Install @google/clasp first."))
            except Exception as e:
                self.after(0, lambda: self._gas_status_var.set("Tools: clasp login error"))
                self.after(0, lambda: messagebox.showerror("clasp login", f"Login failed: {e}"))

        threading.Thread(target=_run, daemon=True).start()

    def _build_marketing_tab(self, parent: ttk.Frame) -> None:
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)

        hud = ttk.LabelFrame(outer, text="Rotation planner (3 streams × 10 templates)")
        hud.pack(fill="x", padx=10, pady=(12, 8))

        # Controls
        row1 = ttk.Frame(hud)
        row1.pack(fill="x", padx=10, pady=(10, 6))

        self._mk_start_var = tk.StringVar(value=date.today().isoformat())
        self._mk_days_var = tk.StringVar(value="10")

        ttk.Label(row1, text="Start date (YYYY-MM-DD):").pack(side="left")
        ttk.Entry(row1, textvariable=self._mk_start_var, width=14).pack(side="left", padx=6)
        ttk.Label(row1, text="Days:").pack(side="left", padx=(10, 0))
        ttk.Entry(row1, textvariable=self._mk_days_var, width=6).pack(side="left", padx=6)

        self._mk_mode_var = tk.StringVar(value="one_per_stream_per_day")
        ttk.Label(row1, text="Mode:").pack(side="left", padx=(14, 0))
        ttk.Radiobutton(row1, text="3 posts/day (A+B+C)", variable=self._mk_mode_var, value="one_per_stream_per_day").pack(
            side="left", padx=6
        )
        ttk.Radiobutton(row1, text="All platforms", variable=self._mk_mode_var, value="all_platforms").pack(side="left")

        ttk.Checkbutton(hud, text="Refine copy (local Ollama)", variable=self._refine_ads_var).pack(anchor="w", padx=10)

        row2 = ttk.Frame(hud)
        row2.pack(fill="x", padx=10, pady=(6, 10))
        ttk.Button(row2, text="Generate 10-day rotation", command=self._mk_generate_rotation).pack(side="left")
        ttk.Button(row2, text="Generate 30-day month", command=lambda: self._mk_generate_rotation(force_days=30)).pack(side="left", padx=8)
        ttk.Button(row2, text="Open output folder", command=self._open_marketing_output).pack(side="left", padx=8)
        ttk.Button(row2, text="Open template folders", command=self._open_marketing_templates).pack(side="left", padx=8)

        links = ttk.LabelFrame(outer, text="Your platform links (used in generated copy)")
        links.pack(fill="x", padx=10, pady=(0, 8))

        self._mk_locanto_var = tk.StringVar(value=(os.getenv("EVIDEX_LINK_LOCANTO") or "https://www.locanto.co.za/by/bboy2306/d10815/").strip())
        self._mk_mya_var = tk.StringVar(value=(os.getenv("EVIDEX_LINK_MYADZ") or "https://myadz.co.za/post-free-ad/").strip())
        self._mk_li_var = tk.StringVar(value=(os.getenv("EVIDEX_LINK_LINKEDIN") or "https://www.linkedin.com/company/evidex23").strip())

        r = ttk.Frame(links)
        r.pack(fill="x", padx=10, pady=8)
        ttk.Button(r, text="Open Locanto", command=lambda: webbrowser.open(self._mk_locanto_var.get().strip())).pack(side="left")
        ttk.Button(r, text="Open MyAdz", command=lambda: webbrowser.open(self._mk_mya_var.get().strip())).pack(side="left", padx=8)
        ttk.Button(r, text="Open LinkedIn", command=lambda: webbrowser.open(self._mk_li_var.get().strip())).pack(side="left")

        # Schedule + preview
        split = ttk.Panedwindow(outer, orient="vertical")
        split.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        top = ttk.Frame(split)
        bottom = ttk.Frame(split)
        split.add(top, weight=3)
        split.add(bottom, weight=2)

        cols = ("day", "stream", "moment", "platform", "status", "views", "reminder", "persona", "template", "headline")
        wrap = ttk.Frame(top)
        wrap.pack(fill="both", expand=True)

        self._mk_tree = ttk.Treeview(wrap, columns=cols, show="headings", height=14)
        ybar = ttk.Scrollbar(wrap, orient="vertical", command=self._mk_tree.yview)
        xbar = ttk.Scrollbar(wrap, orient="horizontal", command=self._mk_tree.xview)
        self._mk_tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        ybar.pack(side="right", fill="y")
        xbar.pack(side="bottom", fill="x")
        self._mk_tree.pack(side="left", fill="both", expand=True)
        self._mk_tree.bind("<<TreeviewSelect>>", lambda _e: self._mk_update_preview())

        self._mk_tree.heading("day", text="Day")
        self._mk_tree.heading("stream", text="Stream")
        self._mk_tree.heading("moment", text="Moment")
        self._mk_tree.heading("platform", text="Platform")
        self._mk_tree.heading("status", text="Status")
        self._mk_tree.heading("views", text="Views")
        self._mk_tree.heading("reminder", text="24h")
        self._mk_tree.heading("persona", text="Persona")
        self._mk_tree.heading("template", text="Template")
        self._mk_tree.heading("headline", text="Headline")

        self._mk_tree.column("day", width=90, anchor="w")
        self._mk_tree.column("stream", width=70, anchor="center")
        self._mk_tree.column("moment", width=150, anchor="w")
        self._mk_tree.column("platform", width=90, anchor="w")
        self._mk_tree.column("status", width=70, anchor="center")
        self._mk_tree.column("views", width=60, anchor="e")
        self._mk_tree.column("reminder", width=70, anchor="center")
        self._mk_tree.column("persona", width=210, anchor="w")
        self._mk_tree.column("template", width=240, anchor="w")
        self._mk_tree.column("headline", width=520, anchor="w")

        try:
            self._mk_tree.tag_configure("live", background="#d6ffd6")
            self._mk_tree.tag_configure("remind", background="#fff1d6")
        except Exception:
            pass

        preview_toolbar = ttk.Frame(bottom)
        preview_toolbar.pack(fill="x", pady=(6, 6))
        ttk.Button(preview_toolbar, text="Copy full post", command=self._mk_copy_selected).pack(side="left")
        ttk.Button(preview_toolbar, text="Open template image", command=self._mk_open_template).pack(side="left", padx=8)
        ttk.Button(preview_toolbar, text="Open posting kit + platform", command=self._mk_open_posting_kit_and_platform).pack(side="left", padx=8)
        ttk.Button(preview_toolbar, text="Open output JSON/CSV", command=self._open_marketing_output).pack(side="left", padx=8)

        tracker = ttk.LabelFrame(bottom, text="Live tracking (paste the ad URL after you post)")
        tracker.pack(fill="x", padx=0, pady=(0, 6))

        self._mk_ad_url_var = tk.StringVar(value="")
        self._mk_status_var = tk.StringVar(value="Status: (none)")
        self._mk_views_var = tk.StringVar(value="Views: (unknown)")
        self._mk_checked_var = tk.StringVar(value="Last checked: (never)")

        tr = ttk.Frame(tracker)
        tr.pack(fill="x", padx=10, pady=8)
        ttk.Label(tr, text="Ad URL:").pack(side="left")
        ttk.Entry(tr, textvariable=self._mk_ad_url_var, width=70).pack(side="left", padx=6)
        ttk.Button(tr, text="Mark Live", command=self._mk_mark_live).pack(side="left", padx=6)
        ttk.Button(tr, text="Check views (best-effort)", command=self._mk_check_views).pack(side="left", padx=6)

        tr2 = ttk.Frame(tracker)
        tr2.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(tr2, textvariable=self._mk_status_var, style="Subtle.TLabel").pack(side="left")
        ttk.Label(tr2, textvariable=self._mk_views_var, style="Subtle.TLabel").pack(side="left", padx=14)
        ttk.Label(tr2, textvariable=self._mk_checked_var, style="Subtle.TLabel").pack(side="left", padx=14)

        self._mk_preview = tk.Text(bottom, height=10, wrap="word")
        self._mk_preview.pack(fill="both", expand=True)
        self._mk_preview.insert("end", "Generate a rotation to preview posts.\n")
        self._mk_preview.configure(state="disabled")

        # Internal state
        self._mk_items: list[dict] = []
        self._mk_last_export: tuple[Path, Path] | None = None
        self._mk_tracker_path = (Path(__file__).resolve().parents[2] / "output" / "marketing" / "ad_tracker.json")
        self._mk_tracker: dict[str, TrackedAd] = load_tracker(self._mk_tracker_path)

        self.after(2000, self._mk_tick)

    def _mk_tick(self) -> None:
        try:
            self._mk_refresh_live_columns()
        finally:
            # Refresh countdown every minute.
            self.after(60000, self._mk_tick)

    def _mk_refresh_live_columns(self) -> None:
        if not hasattr(self, "_mk_tree"):
            return
        # Update status/views/reminder for visible rows.
        now = time.time()

        for iid in self._mk_tree.get_children():
            try:
                idx = int(str(iid))
                if idx < 0 or idx >= len(self._mk_items):
                    continue
                it = self._mk_items[idx]
            except Exception:
                continue

            day = str(it.get("day") or "")
            stream = str(it.get("stream") or "")
            platform = str(it.get("platform") or "")
            k = make_key(day, stream, platform)
            t = self._mk_tracker.get(k)

            status = "planned"
            views = ""
            reminder = ""
            tags: tuple[str, ...] = ()

            if t and t.status == "live":
                status = "live"
                if t.views is not None:
                    views = str(t.views)
                # 24h reminder countdown from when it was first marked live.
                created = float(t.created_ts or now)
                remaining = (created + 24 * 3600) - now
                if remaining <= 0:
                    reminder = "DUE"
                    tags = ("remind",)
                else:
                    hrs = int(remaining // 3600)
                    mins = int((remaining % 3600) // 60)
                    reminder = f"{hrs:02d}:{mins:02d}"
                    tags = ("live",)

            # Update row values in-place
            try:
                vals = list(self._mk_tree.item(iid, "values"))
                # columns: day, stream, moment, platform, status, views, reminder, persona, template, headline
                if len(vals) >= 10:
                    vals[4] = status
                    vals[5] = views
                    vals[6] = reminder
                    self._mk_tree.item(iid, values=tuple(vals), tags=tags)
            except Exception:
                pass

    def _watch_root_path(self) -> Path:
        return Path(self._root_var.get()).expanduser().resolve()

    def _cfg(self) -> WatchConfig:
        root = self._watch_root_path()
        return WatchConfig(
            incoming_dir=root / "incoming",
            processing_dir=root / "processing",
            done_dir=root / "done",
            failed_dir=root / "failed",
            deliveries_dir=root / "deliveries",
        )

    def _browse_root(self) -> None:
        d = filedialog.askdirectory(title="Select watch root")
        if d:
            self._root_var.set(d)
            self._refresh_jobs()

    def _open_root(self) -> None:
        r = self._watch_root_path()
        r.mkdir(parents=True, exist_ok=True)
        _open_path(r)

    def _auto_refresh_tick(self) -> None:
        try:
            if self._auto_refresh_var.get():
                self._refresh_jobs()
        finally:
            self.after(1500, self._auto_refresh_tick)

    def _refresh_jobs(self) -> None:
        root = self._watch_root_path()
        try:
            rows = scan_jobs(root)
        except Exception as e:
            self._watcher_note.set(f"Could not scan jobs: {e}")
            rows = []

        for item in self._tree.get_children():
            self._tree.delete(item)

        for r in rows:
            tags: tuple[str, ...] = ()
            if r.stage == "failed":
                tags = ("failed",)
            elif "error" in (r.flags or ""):
                tags = ("error",)
            elif "retry_later" in (r.flags or ""):
                tags = ("retry",)
            elif r.email_status == "ready" and r.stage == "done" and (not r.emailed):
                tags = ("ready",)
            elif r.email_status in {"blocked_payment"}:
                tags = ("blocked",)
            elif r.stage == "done":
                tags = ("done",)
            elif r.stage == "processing":
                tags = ("processing",)
            else:
                tags = ("incoming",)

            self._tree.insert(
                "",
                "end",
                iid=r.name + "|" + r.stage,
                values=(
                    r.name,
                    r.stage,
                    r.contact,
                    r.email_status,
                    "yes" if r.paid else "no",
                    "yes" if r.deliverable else "no",
                    "yes" if r.emailed else "no",
                    str(r.uploads),
                    f"{r.zip_mb:.1f}" if r.zip_mb else "",
                    r.flags,
                    r.updated,
                ),
                tags=tags,
            )

        self._update_selected_details()
        self._refresh_dashboard()

    def _get_selected_job(self) -> Path | None:
        sel = self._tree.selection()
        if not sel:
            return None
        iid = sel[0]
        # iid format: name|stage
        try:
            name, stage = iid.split("|", 1)
        except ValueError:
            return None
        job_dir = self._watch_root_path() / stage / name
        if job_dir.exists():
            return job_dir
        return None

    def _open_selected_job(self) -> None:
        job_dir = self._get_selected_job()
        if not job_dir:
            return
        _open_path(job_dir)

    def _open_payment_link(self) -> None:
        # Prefer explicit PAYMENT_LINK; otherwise PAYPAL_LINK.
        url = (os.getenv("PAYMENT_LINK") or os.getenv("PAYPAL_LINK") or "").strip()
        if not url or url == "(add payment link)":
            messagebox.showinfo(
                "No payment link",
                "Set PAYMENT_LINK or PAYPAL_LINK in your environment (or .env/run config) to enable one-click opening.",
            )
            return
        _open_url(url)

    def _open_gmail_inbox(self) -> None:
        _open_url("https://mail.google.com/mail/u/0/#inbox")

    def _open_selected_zip(self) -> None:
        job_dir = self._get_selected_job()
        if not job_dir:
            return
        z = job_dir / "DELIVERABLE.zip"
        if z.exists():
            _open_path(z)
            return
        messagebox.showinfo("No deliverable", "DELIVERABLE.zip not found in this job folder yet.")

    def _mark_selected_paid(self) -> None:
        job_dir = self._get_selected_job()
        if not job_dir:
            return
        p = job_dir / "PAID.txt"
        try:
            note = simpledialog.askstring(
                "Mark paid",
                "Optional note (amount, PayPal txn id, payer email). Leave blank if not needed:",
                parent=self,
            )
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            body = "Paid (manual) at " + ts + "\n"
            if note and str(note).strip():
                body += "Note: " + str(note).strip() + "\n"
                (job_dir / "PAYMENT_RECEIPT.txt").write_text(body, encoding="utf-8")
            p.write_text(body, encoding="utf-8")
            self._refresh_jobs()
        except Exception as e:
            messagebox.showerror("Failed", f"Could not write PAID.txt: {e}")

    def _update_selected_details(self) -> None:
        job_dir = self._get_selected_job()
        text = self._build_job_details_text(job_dir) if job_dir else "Select a job row to see details.\n"
        self._details.configure(state="normal")
        self._details.delete("1.0", "end")
        self._details.insert("end", text)
        self._details.configure(state="disabled")

    def _read_small(self, path: Path, *, limit: int = 4000) -> str:
        try:
            s = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        s = s.strip()
        if len(s) > limit:
            s = s[:limit] + "\n…(truncated)…\n"
        return s

    def _build_job_details_text(self, job_dir: Path) -> str:
        if not job_dir.exists():
            return "(job folder not found)\n"
        stage = job_dir.parent.name
        bits: list[str] = []
        bits.append(f"Job: {job_dir.name}")
        bits.append(f"Stage: {stage}")
        bits.append(f"Path: {job_dir}")
        bits.append("")

        paid = (job_dir / "PAID.txt").exists()
        deliverable = (job_dir / "DELIVERABLE.zip").exists()
        emailed = (job_dir / "SENT.txt").exists()
        bits.append(f"Paid: {'yes' if paid else 'no'}")
        bits.append(f"Deliverable.zip: {'yes' if deliverable else 'no'}")
        bits.append(f"SENT.txt (emailed): {'yes' if emailed else 'no'}")
        bits.append("")

        # Common metadata files
        for fname in ["CONTACT_EMAIL.txt", "DELIVERY_EMAIL.txt"]:
            p = job_dir / fname
            if p.exists():
                bits.append(f"--- {fname} ---")
                bits.append(self._read_small(p) or "(empty)")
                bits.append("")

        # Payment receipt details (optional)
        for fname in ["PAID.txt", "PAYMENT_RECEIPT.txt"]:
            p = job_dir / fname
            if p.exists():
                bits.append(f"--- {fname} ---")
                bits.append(self._read_small(p) or "(empty)")
                bits.append("")

        # Errors / retry flags
        for fname in ["UPLOADS_ACTION_REQUIRED.txt", "RETRY_LATER.txt", "ERROR.txt"]:
            p = job_dir / fname
            if p.exists():
                bits.append(f"--- {fname} ---")
                bits.append(self._read_small(p) or "(empty)")
                bits.append("")

        intake = job_dir / "intake.yaml"
        if intake.exists():
            bits.append("--- intake.yaml (preview) ---")
            bits.append(self._read_small(intake, limit=2500) or "(empty)")
            bits.append("")

        return "\n".join(bits).rstrip() + "\n"

    def _apply_require_payment(self) -> None:
        os.environ["REQUIRE_PAYMENT"] = "1" if self._require_payment_var.get() else "0"
        self._refresh_jobs()

    def _apply_ai_enable(self) -> None:
        # Global kill switch implemented in LlmClient.enabled
        os.environ["LLM_DISABLED"] = "0" if self._ai_enabled_var.get() else "1"
        self._refresh_jobs()

    def _apply_ai_modes(self) -> None:
        os.environ["SUMMARY_USE_LLM"] = "1" if self._summary_ai_var.get() else "0"
        os.environ["NARRATIVE_USE_LLM"] = "1" if self._narrative_ai_var.get() else "0"

    def _apply_ai_endpoint_settings(self) -> None:
        base_raw = (self._ollama_base_url_var.get() or "").strip()
        base = _normalize_openai_base_url(base_raw)
        model = (self._ollama_model_var.get() or "").strip()
        if base:
            os.environ["OLLAMA_BASE_URL"] = base
            if base_raw and base_raw.rstrip("/") != base:
                # Keep the UI consistent with what we actually use.
                try:
                    self._ollama_base_url_var.set(base)
                except Exception:
                    pass
        if model:
            os.environ["OLLAMA_MODEL"] = model

        sm = (self._ollama_model_summary_var.get() or "").strip()
        nm = (self._ollama_model_narrative_var.get() or "").strip()
        if sm:
            os.environ["OLLAMA_MODEL_SUMMARY"] = sm
        if nm:
            os.environ["OLLAMA_MODEL_NARRATIVE"] = nm

    def _apply_ui_settings_to_env(self) -> None:
        self._apply_require_payment()
        self._apply_ai_enable()
        self._apply_ai_modes()
        self._apply_ai_endpoint_settings()
        self._apply_google_settings()
        self._apply_gas_settings()
        # Don't overwrite env unless user provides values
        if self._payment_link_var.get().strip():
            os.environ["PAYMENT_LINK"] = self._payment_link_var.get().strip()
        if self._paypal_link_var.get().strip():
            os.environ["PAYPAL_LINK"] = self._paypal_link_var.get().strip()

    def _save_ai_settings(self) -> None:
        path = default_env_file_path()
        updates: dict[str, str] = {}

        base_raw = (self._ollama_base_url_var.get() or "").strip()
        base = _normalize_openai_base_url(base_raw)
        model = (self._ollama_model_var.get() or "").strip()
        sm = (self._ollama_model_summary_var.get() or "").strip()
        nm = (self._ollama_model_narrative_var.get() or "").strip()
        exe = (self._ollama_exe_var.get() or "").strip()

        if base:
            updates["OLLAMA_BASE_URL"] = base
            os.environ["OLLAMA_BASE_URL"] = base
            if base_raw and base_raw.rstrip("/") != base:
                try:
                    self._ollama_base_url_var.set(base)
                except Exception:
                    pass
        if model:
            updates["OLLAMA_MODEL"] = model
            os.environ["OLLAMA_MODEL"] = model
        if sm:
            updates["OLLAMA_MODEL_SUMMARY"] = sm
            os.environ["OLLAMA_MODEL_SUMMARY"] = sm
        if nm:
            updates["OLLAMA_MODEL_NARRATIVE"] = nm
            os.environ["OLLAMA_MODEL_NARRATIVE"] = nm

        if exe:
            updates["OLLAMA_EXE_PATH"] = exe
            os.environ["OLLAMA_EXE_PATH"] = exe

        # Persist AI toggles as well (so watcher/CLI match the UI)
        updates["LLM_DISABLED"] = "0" if self._ai_enabled_var.get() else "1"
        updates["SUMMARY_USE_LLM"] = "1" if self._summary_ai_var.get() else "0"
        updates["NARRATIVE_USE_LLM"] = "1" if self._narrative_ai_var.get() else "0"

        try:
            save_env_file(path, updates)
            self._env_path = path
            messagebox.showinfo("Saved", f"Saved AI settings to: {path}")
        except Exception as e:
            messagebox.showerror("Failed", f"Could not save AI settings: {e}")

    def _check_llm_endpoint(self) -> None:
        base_raw = (self._ollama_base_url_var.get() or "").strip()
        base = _normalize_openai_base_url(base_raw)
        if not base:
            self._ollama_status_var.set("LLM endpoint: missing OLLAMA_BASE_URL (try http://localhost:11434/v1)")
            return

        def _probe(url: str) -> tuple[bool, str]:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "EVIDEX"})
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    code = int(getattr(resp, "status", 200))
                    if 200 <= code < 300:
                        return True, f"OK ({url})"
                    return False, f"HTTP {code} ({url})"
            except urllib.error.URLError as e:
                return False, f"not reachable ({url}) — {e}"
            except Exception as e:
                return False, f"check failed ({url}) — {e}"

        # EVIDEX uses the OpenAI-compatible endpoints (/v1/models).
        candidates: list[str] = [base + "/models"]

        # Also probe native Ollama endpoint for better diagnostics.
        # Native endpoint: http://localhost:11434/api/tags
        # (Not used by the OpenAI client, but helps confirm the server is actually up.)
        if "/v1" in base:
            native = base.split("/v1", 1)[0]
        else:
            native = base
        candidates.append(native + "/api/tags")

        for url in candidates:
            ok, msg = _probe(url)
            if ok:
                note = ""
                if base_raw and base_raw.rstrip("/") != base:
                    note = " (normalized to /v1)"
                self._ollama_status_var.set(f"LLM endpoint: {msg}{note}")
                return

        self._ollama_status_var.set(
            "LLM endpoint not found. Fixes: start Ollama, then set OLLAMA_BASE_URL=http://localhost:11434/v1, and ensure the model exists (Pull model)."
        )

    def _start_ollama_server(self) -> None:
        # On many Windows installs Ollama runs as a background service; "ollama serve" is a no-op.
        # We still offer this as a convenience for dev machines.
        try:
            if self._ollama_proc is not None and self._ollama_proc.poll() is None:
                self._ollama_status_var.set("Ollama: server already started (this session)")
                return
        except Exception:
            pass

        try:
            creationflags = 0
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                creationflags |= subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

            self._ollama_proc = subprocess.Popen(
                [self._ollama_cmd_(), "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self._ollama_status_var.set("Ollama: starting server…")
            self.after(900, self._check_llm_endpoint)
        except FileNotFoundError:
            messagebox.showerror(
                "Ollama not found",
                "Could not find Ollama executable.\n\nFix by either:\n- Installing Ollama (https://ollama.com/download)\n- Or setting OLLAMA_EXE_PATH to your ollama.exe path in the UI\n",
            )
        except Exception as e:
            messagebox.showerror("Failed", f"Could not start Ollama: {e}")

    def _pull_ollama_model(self) -> None:
        model = (self._ollama_model_var.get() or "").strip()
        if not model:
            messagebox.showinfo("Missing model", "Set OLLAMA_MODEL first (e.g. llama3.2:latest).")
            return
        try:
            self._ollama_status_var.set(f"Ollama: pulling {model}…")
            out = subprocess.run([self._ollama_cmd_(), "pull", model], capture_output=True, text=True, timeout=60 * 20)
            if out.returncode == 0:
                self._ollama_status_var.set(f"Ollama: model ready ({model})")
                messagebox.showinfo("Model ready", f"Pulled: {model}")
            else:
                self._ollama_status_var.set(f"Ollama: pull failed ({model})")
                messagebox.showerror("Pull failed", (out.stderr or out.stdout or "(no output)")[:3500])
        except FileNotFoundError:
            messagebox.showerror(
                "Ollama not found",
                "Could not find Ollama executable. Set OLLAMA_EXE_PATH in the UI, or install Ollama: https://ollama.com/download",
            )
        except subprocess.TimeoutExpired:
            messagebox.showerror("Timeout", "Model pull took too long. Try again, or pull from a terminal: ollama pull <model>")
        except Exception as e:
            messagebox.showerror("Failed", f"Could not pull model: {e}")

    def _test_llm_completion(self) -> None:
        # Uses the same OpenAI-compatible client path as pack generation.
        try:
            self._apply_ai_enable()
            self._apply_ai_endpoint_settings()
            from .llm import LlmClient, load_llm_config

            cfg = load_llm_config()
            client = LlmClient(cfg)
            text = client.complete(
                system="You are a concise assistant.",
                user="Reply with the single word: OK",
                model=(os.getenv("OLLAMA_MODEL") or os.getenv("OPENAI_MODEL") or None),
            )
            if not text:
                extra = ""
                try:
                    if getattr(client, "last_error", None):
                        extra = f"\n\nDetails: {client.last_error}"
                except Exception:
                    pass
                messagebox.showwarning(
                    "No response",
                    "The LLM returned no text.\n\nCheck that:\n- Enable AI is ON\n- OLLAMA_BASE_URL ends with /v1\n- The model exists (try Pull model)\n" + extra,
                )
                return
            messagebox.showinfo("LLM response", text[:1500])
        except Exception as e:
            messagebox.showerror("Test failed", f"Could not run test completion: {e}")

    def _ollama_cmd_(self) -> str:
        cmd = (self._ollama_exe_var.get() or "").strip()
        if not cmd:
            cmd = (os.getenv("OLLAMA_EXE_PATH") or "ollama").strip() or "ollama"
        return cmd

    def _browse_ollama_exe(self) -> None:
        p = filedialog.askopenfilename(
            title="Select ollama.exe",
            filetypes=[("Ollama", "ollama.exe"), ("Executables", "*.exe"), ("All files", "*")],
        )
        if p:
            self._ollama_exe_var.set(p)

    def _save_payment_links(self) -> None:
        path = default_env_file_path()
        updates: dict[str, str] = {}
        if self._payment_link_var.get().strip():
            updates["PAYMENT_LINK"] = self._payment_link_var.get().strip()
            os.environ["PAYMENT_LINK"] = updates["PAYMENT_LINK"]
        if self._paypal_link_var.get().strip():
            updates["PAYPAL_LINK"] = self._paypal_link_var.get().strip()
            os.environ["PAYPAL_LINK"] = updates["PAYPAL_LINK"]
        if not updates:
            messagebox.showinfo("Nothing to save", "Enter a Payment link and/or PayPal link first.")
            return

        try:
            save_env_file(path, updates)
            self._env_path = path
            messagebox.showinfo("Saved", f"Saved to: {path}")
        except Exception as e:
            messagebox.showerror("Failed", f"Could not save settings: {e}")

    def _apply_google_settings(self) -> None:
        form_id = (self._google_form_id_var.get() or "").strip()
        folder_id = (self._google_drive_folder_id_var.get() or "").strip()
        if form_id:
            os.environ["EVIDEX_GOOGLE_FORM_ID"] = form_id
        if folder_id:
            os.environ["EVIDEX_GOOGLE_DRIVE_FOLDER_ID"] = folder_id

    def _apply_gas_settings(self) -> None:
        cmd = (self._clasp_cmd_var.get() or "").strip() or "clasp"
        proj = (self._gas_project_dir_var.get() or "").strip()
        os.environ["EVIDEX_CLASP_CMD"] = cmd
        if proj:
            os.environ["EVIDEX_GAS_PROJECT_DIR"] = proj

    def _save_google_settings(self) -> None:
        path = default_env_file_path()
        updates: dict[str, str] = {}
        form_id = (self._google_form_id_var.get() or "").strip()
        folder_id = (self._google_drive_folder_id_var.get() or "").strip()

        if form_id:
            updates["EVIDEX_GOOGLE_FORM_ID"] = form_id
            os.environ["EVIDEX_GOOGLE_FORM_ID"] = form_id
        if folder_id:
            updates["EVIDEX_GOOGLE_DRIVE_FOLDER_ID"] = folder_id
            os.environ["EVIDEX_GOOGLE_DRIVE_FOLDER_ID"] = folder_id

        if not updates:
            messagebox.showinfo("Nothing to save", "Enter a Google Form ID/URL and/or Drive folder ID/URL first.")
            return

        try:
            save_env_file(path, updates)
            self._env_path = path
            messagebox.showinfo("Saved", f"Saved to: {path}")
        except Exception as e:
            messagebox.showerror("Failed", f"Could not save settings: {e}")

    def _save_gas_settings(self) -> None:
        path = default_env_file_path()
        self._apply_gas_settings()
        updates = {
            "EVIDEX_CLASP_CMD": os.getenv("EVIDEX_CLASP_CMD", "clasp"),
            "EVIDEX_GAS_PROJECT_DIR": os.getenv("EVIDEX_GAS_PROJECT_DIR", ""),
        }
        try:
            save_env_file(path, updates)
            self._env_path = path
            messagebox.showinfo("Saved", f"Saved to: {path}")
        except Exception as e:
            messagebox.showerror("Failed", f"Could not save settings: {e}")

    def _open_google_form(self) -> None:
        self._apply_google_settings()
        if self._google_remember_var.get():
            try:
                self._save_google_settings()
            except Exception:
                pass
        url = _google_form_url(os.getenv("EVIDEX_GOOGLE_FORM_ID", ""))
        if not url:
            messagebox.showinfo("Missing form", "Set EVIDEX_GOOGLE_FORM_ID (or paste a full form URL) first.")
            return
        _open_url(url)

    def _open_google_drive_folder(self) -> None:
        self._apply_google_settings()
        if self._google_remember_var.get():
            try:
                self._save_google_settings()
            except Exception:
                pass
        url = _google_drive_folder_url(os.getenv("EVIDEX_GOOGLE_DRIVE_FOLDER_ID", ""))
        if not url:
            messagebox.showinfo("Missing folder", "Set EVIDEX_GOOGLE_DRIVE_FOLDER_ID (or paste a full folder URL) first.")
            return
        _open_url(url)

    def _open_form_to_drive_setup_guide(self) -> None:
        try:
            p = _repo_root() / "scripts" / "google_forms" / "FORM_TO_DRIVE_SETUP.md"
            if not p.exists():
                messagebox.showinfo("Missing guide", f"Could not find: {p}")
                return
            _open_path(p)
        except Exception as e:
            messagebox.showerror("Failed", f"Could not open guide: {e}")

    def _ensure_watch_root_folders(self, root: Path) -> list[str]:
        required = ["incoming", "processing", "done", "failed", "deliveries"]
        created: list[str] = []
        for name in required:
            p = root / name
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                created.append(name)
        return created

    def _google_setup_checklist(self) -> None:
        """Guided checklist to reduce 'missed a step' risk for the Google pipeline."""

        # Make sure UI values are reflected into env (no persistence required).
        try:
            self._apply_google_settings()
            self._apply_gas_settings()
        except Exception:
            pass

        notes: list[str] = []

        # 1) Local mirror root
        try:
            root = self._watch_root_path()
            if not root.exists():
                root.mkdir(parents=True, exist_ok=True)
                notes.append(f"✅ Created watch root: {root}")
            created = self._ensure_watch_root_folders(root)
            if created:
                notes.append(f"✅ Created missing folders: {', '.join(created)}")
            else:
                notes.append("✅ Watch root folders present (incoming/processing/done/failed/deliveries)")
        except Exception as e:
            notes.append(f"❌ Watch root check failed: {e}")

        # 2) Form + Drive IDs
        form = (os.getenv("EVIDEX_GOOGLE_FORM_ID") or "").strip()
        folder = (os.getenv("EVIDEX_GOOGLE_DRIVE_FOLDER_ID") or "").strip()
        if form:
            notes.append("✅ Google Form ID/URL set")
        else:
            notes.append("⚠️ Google Form ID/URL not set (paste it in Watcher tab → Google Form ID/URL)")

        if folder:
            notes.append("✅ Drive root folder ID/URL set")
        else:
            notes.append("⚠️ Drive folder ID/URL not set (paste it in Watcher tab → Drive folder ID/URL)")

        # 3) Apps Script automation (optional)
        proj, err = self._gas_require_config()
        if proj is None:
            notes.append("ℹ️ Apps Script automation not configured in UI (optional).")
            notes.append(f"   To enable one-click trigger setup: {err}")
            notes.append("   Tip: Use Watcher tab → Google Apps Script → 'Init clasp project…' to create a project folder.")
        else:
            # clasp availability check
            try:
                out = subprocess.run([self._gas_cmd(), "--version"], capture_output=True, text=True, timeout=15)
                if out.returncode == 0:
                    v = (out.stdout or out.stderr or "").strip().splitlines()[0:1]
                    notes.append(f"✅ clasp detected ({' '.join(v) if v else 'ok'})")
                else:
                    notes.append("⚠️ clasp command exists but returned error (try installing @google/clasp + running clasp login)")
            except FileNotFoundError:
                notes.append("⚠️ clasp not found. Install: Node.js + `npm i -g @google/clasp`, then `clasp login`.")
            except Exception as e:
                notes.append(f"⚠️ clasp check failed: {type(e).__name__}: {e}")

            notes.append("✅ clasp project dir looks valid (.clasp.json present)")
            notes.append("   Next: click 'Push code' then click 'Configure IDs', then run setupTrigger()/setupDeliveryTrigger()")

        msg = (
            "This checklist verifies the parts EVIDEX can check locally.\n\n"
            + "\n".join(notes)
            + "\n\nManual steps (cannot be fully automated):\n"
            "- Install & sign in to Google Drive for Desktop\n"
            "- Mirror the Drive folder you set in 'Drive folder ID/URL' to the same local path as Watch root\n"
            "- Confirm you see incoming/processing/done/failed/deliveries locally before starting the watcher\n"
            "- Tip: click 'Verify mirror' to create a probe file and (optionally) check it appears in Drive\n"
        )

        messagebox.showinfo("Google intake setup checklist", msg)

    def _browse_gas_project_dir(self) -> None:
        p = filedialog.askdirectory(title="Select Apps Script (clasp) project folder")
        if p:
            self._gas_project_dir_var.set(p)

    def _gas_cmd(self) -> str:
        raw = (self._clasp_cmd_var.get() or os.getenv("EVIDEX_CLASP_CMD") or "clasp").strip() or "clasp"
        resolved = _resolve_windows_cmd(raw) or _resolve_windows_cmd("clasp")
        return resolved or raw

    def _gas_project_dir(self) -> Path | None:
        raw = (self._gas_project_dir_var.get() or os.getenv("EVIDEX_GAS_PROJECT_DIR") or "").strip()
        if not raw:
            return None
        try:
            p = Path(raw).expanduser().resolve()
        except Exception:
            return None
        if not p.exists() or not p.is_dir():
            return None
        return p

    def _gas_require_config(self) -> tuple[Path | None, str]:
        proj = self._gas_project_dir()
        if proj is None:
            return None, "Set Project dir first (a folder containing .clasp.json)."
        if not (proj / ".clasp.json").exists():
            return None, "Project dir must contain .clasp.json (use `clasp clone` or initialize a clasp project)."
        return proj, ""

    def _gas_default_project_dir(self) -> Path:
        # A safe default inside the repo so users don't need to pre-create anything.
        return (_repo_root() / "_gas_project").resolve()

    def _gas_repo_scripts_dir(self) -> Path:
        return (_repo_root() / "scripts" / "google_forms").resolve()

    def _gas_copy_repo_scripts(self, target_dir: Path) -> None:
        src = self._gas_repo_scripts_dir()
        if not src.exists():
            raise FileNotFoundError(f"Missing scripts dir: {src}")

        target_dir.mkdir(parents=True, exist_ok=True)

        # Copy scripts and supporting files into the clasp project.
        # Including appsscript.json ensures Execution API is enabled for `clasp run`.
        for p in src.iterdir():
            if not p.is_file():
                continue
            suf = p.suffix.lower()
            if suf in (".gs", ".json", ".md"):
                shutil.copy2(str(p), str(target_dir / p.name))

        # Remove default clasp scaffolding if present to keep the project tidy.
        for name in ("Code.js",):
            try:
                q = target_dir / name
                if q.exists():
                    q.unlink()
            except Exception:
                pass

    def _gas_clasp_run(self, args: list[str], *, cwd: Path, timeout_s: int = 60 * 5) -> subprocess.CompletedProcess[str]:
        cmd = [self._gas_cmd(), *args]
        return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s)

    def _gas_init_wizard(self) -> None:
        """Guided init to create a clasp project folder and push our .gs scripts."""

        # Ensure current UI values are applied.
        try:
            self._apply_google_settings()
            self._apply_gas_settings()
        except Exception:
            pass

        proj = self._gas_project_dir()
        if proj is None:
            proj = self._gas_default_project_dir()
            self._gas_project_dir_var.set(str(proj))
            try:
                self._apply_gas_settings()
            except Exception:
                pass

        proj = Path(str(proj)).expanduser().resolve()
        proj.mkdir(parents=True, exist_ok=True)

        already = (proj / ".clasp.json").exists()
        if already:
            ok = messagebox.askyesno(
                "Use existing project?",
                "This folder already contains .clasp.json.\n\n"
                "Do you want to copy the EVIDEX .gs scripts into this project and push them?",
            )
        else:
            ok = messagebox.askyesno(
                "Initialize clasp project?",
                "EVIDEX can initialize a new Google Apps Script project using clasp in:\n\n"
                f"{proj}\n\n"
                "You must have Node.js + @google/clasp installed and be logged in (clasp login).\n\n"
                "Continue?",
            )
        if not ok:
            return

        self._gas_status_var.set("Apps Script: initializing…")

        def _run() -> None:
            try:
                # 1) Verify clasp exists
                try:
                    ver = self._gas_clasp_run(["--version"], cwd=proj, timeout_s=15)
                except FileNotFoundError:
                    self.after(0, lambda: self._gas_status_var.set("Apps Script: clasp not found (install @google/clasp)"))
                    return

                if ver.returncode != 0:
                    msg = (ver.stderr or ver.stdout or "").strip()
                    self.after(0, lambda m=msg: self._gas_status_var.set(f"Apps Script: clasp check failed: {m[:220]}"))
                    return

                # 2) Create project if needed
                if not (proj / ".clasp.json").exists():
                    out = self._gas_clasp_run(["create", "--type", "standalone", "--title", "EVIDEX Automation"], cwd=proj, timeout_s=60 * 5)
                    if out.returncode != 0:
                        msg = (out.stderr or out.stdout or "(no output)").strip()
                        msg_l = msg.lower()
                        hint = " (did you run `clasp login`?)" if ("login" in msg_l or "auth" in msg_l) else ""
                        api_related = (
                            ("script.googleapis.com" in msg_l)
                            or ("apps script api" in msg_l)
                            or ("apps-script" in msg_l and "api" in msg_l)
                        ) and (
                            ("not been used" in msg_l)
                            or ("disabled" in msg_l)
                            or ("enable" in msg_l)
                            or ("access not configured" in msg_l)
                        )

                        def _maybe_help_api() -> None:
                            if not api_related:
                                return
                            steps = (
                                "Google is rejecting `clasp create` because the Google Apps Script API is not enabled.\n\n"
                                "Fix (one-time):\n"
                                "1) Open Apps Script user settings and turn ON: 'Google Apps Script API'\n"
                                "2) In Google Cloud Console, enable API: Google Apps Script API\n\n"
                                "Links:\n"
                                "- https://script.google.com/home/usersettings\n"
                                "- https://console.cloud.google.com/apis/library/script.googleapis.com\n\n"
                                "Then re-run: Google tab → 'Init clasp project…'"
                            )
                            messagebox.showerror("Enable Apps Script API", steps)
                            if messagebox.askyesno("Open user settings?", "Open Apps Script user settings now?"):
                                _open_url("https://script.google.com/home/usersettings")
                            if messagebox.askyesno("Open API library?", "Open Cloud Console API library now?"):
                                _open_url("https://console.cloud.google.com/apis/library/script.googleapis.com")

                        self.after(0, lambda m=msg, h=hint: self._gas_status_var.set(f"Apps Script: create failed{h}: {m[:220]}"))
                        self.after(0, _maybe_help_api)
                        return

                # 3) Copy our scripts into the project
                self._gas_copy_repo_scripts(proj)

                # 4) Push
                push = self._gas_clasp_run(["push", "-f"], cwd=proj, timeout_s=60 * 5)
                if push.returncode != 0:
                    msg = (push.stderr or push.stdout or "(no output)").strip()
                    self.after(0, lambda m=msg: self._gas_status_var.set(f"Apps Script: push failed: {m[:220]}"))
                    return

                self.after(0, lambda: self._gas_status_var.set("Apps Script: initialized + pushed"))
            except Exception as e:
                self.after(0, lambda: self._gas_status_var.set(f"Apps Script: init error ({type(e).__name__})"))

        threading.Thread(target=_run, daemon=True).start()

    def _gas_configure_ids(self) -> None:
        """Set Script Properties FORM_ID and EVIDENCE_ENGINE_ROOT_FOLDER_ID via clasp run."""

        # Pull from UI/env (can be URLs); extract just the IDs.
        self._apply_google_settings()
        form_raw = os.getenv("EVIDEX_GOOGLE_FORM_ID", "")
        folder_raw = os.getenv("EVIDEX_GOOGLE_DRIVE_FOLDER_ID", "")
        form_id = _extract_google_form_id(form_raw)
        folder_id = _extract_google_drive_folder_id(folder_raw)

        if not form_id and not folder_id:
            messagebox.showinfo(
                "Missing IDs",
                "Set at least one of:\n- Google Form ID/URL\n- Drive folder ID/URL\n\nThen click Configure IDs.",
            )
            return

        proj, err = self._gas_require_config()
        if proj is None:
            messagebox.showinfo("Apps Script not configured", err)
            return

        params = json.dumps([form_id, folder_id])
        self._gas_status_var.set("Apps Script: configuring IDs…")

        def _run() -> None:
            try:
                out = self._gas_clasp_run(["run", "evidexConfigure", "--params", params], cwd=proj, timeout_s=60 * 5)
                if out.returncode == 0:
                    self.after(0, lambda: self._gas_status_var.set("Apps Script: IDs configured"))
                else:
                    msg = (out.stderr or out.stdout or "(no output)").strip()
                    self.after(0, lambda m=msg: self._gas_status_var.set(f"Apps Script: configure failed: {m[:220]}"))
            except FileNotFoundError:
                self.after(0, lambda: self._gas_status_var.set("Apps Script: clasp not found (install @google/clasp)"))
            except Exception:
                self.after(0, lambda: self._gas_status_var.set("Apps Script: configure error"))

        threading.Thread(target=_run, daemon=True).start()

    def _gas_create_intake_form(self) -> None:
        """Create the EVIDEX intake form via Apps Script (clasp) and populate the Form ID field."""

        proj, err = self._gas_require_config()
        if proj is None:
            messagebox.showinfo("Apps Script not configured", err)
            return

        self._gas_status_var.set("Apps Script: creating form…")

        def _extract_json(text: str) -> dict | None:
            s = (text or "").strip()
            if not s:
                return None
            # clasp prints other lines; try to locate a JSON object.
            start = s.find("{")
            end = s.rfind("}")
            if start < 0 or end < 0 or end <= start:
                return None
            try:
                return json.loads(s[start : end + 1])
            except Exception:
                return None

        def _run() -> None:
            try:
                out = self._gas_clasp_run(["run", "evidexCreateIntakeForm"], cwd=proj, timeout_s=60 * 8)
                if out.returncode != 0:
                    msg = (out.stderr or out.stdout or "(no output)").strip()
                    self.after(0, lambda m=msg: self._gas_status_var.set(f"Apps Script: create form failed: {m[:220]}"))                    
                    def _maybe_help() -> None:
                        ml = msg.lower()
                        if "unable to run script function" in ml and "permission" in ml:
                            help_msg = (
                                "Apps Script is not authorized to run via the API yet.\n\n"
                                "Fix (one-time):\n"
                                "1) Click 'Open editor' in the Google tab\n"
                                "2) In the Apps Script editor, select any function (e.g. evidexShowConfig) and click Run\n"
                                "3) Approve the permission prompts\n\n"
                                "Then retry: 'Create intake form'."
                            )
                            messagebox.showwarning("Authorize Apps Script", help_msg)
                    self.after(0, _maybe_help)
                    return

                raw = ((out.stdout or "") + "\n" + (out.stderr or "")).strip()

                payload = _extract_json(raw)
                form_id = ""
                edit_url = ""
                view_url = ""
                if isinstance(payload, dict):
                    form_id = str(payload.get("formId") or "").strip()
                    edit_url = str(payload.get("editUrl") or "").strip()
                    view_url = str(payload.get("viewUrl") or "").strip()

                if form_id:
                    self.after(0, lambda fid=form_id: self._google_form_id_var.set(fid))
                    self.after(0, self._apply_google_settings)
                    if self._google_remember_var.get():
                        try:
                            self.after(0, self._save_google_settings)
                        except Exception:
                            pass

                if not form_id:
                    self.after(0, lambda: self._gas_status_var.set("Apps Script: create form returned no Form ID"))

                    def _warn() -> None:
                        msg = (
                            "The Apps Script call returned success, but EVIDEX could not extract a Form ID from the output.\n\n"
                            "Most common causes:\n"
                            "- The script ran under a different Google account than the one you’re viewing in Google Forms\n"
                            "- The function didn’t return JSON (old code not pushed / wrong project)\n\n"
                            "Try:\n"
                            "1) Click 'Open editor' and confirm you’re in the expected Google account\n"
                            "2) Click 'Push code' then retry 'Create intake form'\n"
                            "3) If a browser is already signed into multiple accounts, open forms.google.com in an Incognito window\n\n"
                            "Raw output (last ~1200 chars):\n"
                            + (raw[-1200:] if raw else "(no output)")
                        )
                        messagebox.showwarning("Create form (no Form ID)", msg)

                    self.after(0, _warn)
                    return

                self.after(0, lambda: self._gas_status_var.set("Apps Script: intake form created"))

                def _msg() -> None:
                    lines = ["Created the EVIDEX intake form."]
                    if form_id:
                        lines.append(f"Form ID: {form_id}")
                    if edit_url:
                        lines.append(f"Edit URL: {edit_url}")
                    if view_url:
                        lines.append(f"Public URL: {view_url}")
                    messagebox.showinfo("Form created", "\n".join(lines))
                    if view_url:
                        if messagebox.askyesno("Open form?", "Open the public form URL now?"):
                            _open_url(view_url)
                    elif edit_url:
                        if messagebox.askyesno("Open form?", "Open the form edit URL now?"):
                            _open_url(edit_url)

                self.after(0, _msg)
            except FileNotFoundError:
                self.after(0, lambda: self._gas_status_var.set("Apps Script: clasp not found (install @google/clasp)"))
            except Exception:
                self.after(0, lambda: self._gas_status_var.set("Apps Script: create form error"))

        threading.Thread(target=_run, daemon=True).start()

    def _verify_drive_mirror(self) -> None:
        """Best-effort mirror verification.

        1) Create a local probe file in the watch root.
        2) If clasp is configured and EVIDENCE_ENGINE_ROOT_FOLDER_ID is set in Script Properties,
           poll Apps Script to see if the probe shows up in Drive.
        """

        try:
            self._apply_google_settings()
            self._apply_gas_settings()
        except Exception:
            pass

        root = self._watch_root_path()
        try:
            root.mkdir(parents=True, exist_ok=True)
            self._ensure_watch_root_folders(root)
        except Exception as e:
            messagebox.showerror("Verify mirror", f"Could not create watch root folders: {e}")
            return

        probe_name = "_EVIDEX_MIRROR_PROBE.txt"
        probe_path = root / probe_name
        try:
            probe_path.write_text(
                f"EVIDEX mirror probe\nCreated: {datetime.now().isoformat()}\n",
                encoding="utf-8",
            )
        except Exception as e:
            messagebox.showerror("Verify mirror", f"Could not write probe file: {e}")
            return

        # If clasp isn't configured, we can only provide a manual confirmation path.
        proj, err = self._gas_require_config()
        folder_raw = os.getenv("EVIDEX_GOOGLE_DRIVE_FOLDER_ID", "")
        folder_id = _extract_google_drive_folder_id(folder_raw)
        if proj is None or not folder_id:
            extra = []
            if not folder_id:
                extra.append("- Set Drive folder ID/URL in Watcher tab")
            if proj is None:
                extra.append("- Configure Apps Script (Init clasp project… then Configure IDs)")
            msg = (
                f"Created local probe file:\n{probe_path}\n\n"
                "To verify mirroring manually:\n"
                "1) Wait ~30–120 seconds for Drive sync\n"
                "2) Open your Drive folder in the browser\n"
                f"3) Confirm you see: {probe_name}\n\n"
                "For automated verification, also configure clasp + Script Properties.\n"
                + ("\n".join(extra) if extra else "")
            )
            messagebox.showinfo("Verify mirror (manual)", msg)
            return

        # Automated verification via Apps Script (poll for up to ~2 minutes)
        self._gas_status_var.set("Apps Script: verifying mirror…")

        def _extract_json(text: str) -> dict | None:
            s = (text or "").strip()
            if not s:
                return None
            start = s.find("{")
            end = s.rfind("}")
            if start < 0 or end < 0 or end <= start:
                return None
            try:
                return json.loads(s[start : end + 1])
            except Exception:
                return None

        def _run() -> None:
            try:
                params = json.dumps([probe_name])
                deadline = time.time() + 120.0
                found = False
                last_msg = ""

                while time.time() < deadline:
                    out = self._gas_clasp_run(["run", "evidexMirrorProbe", "--params", params], cwd=proj, timeout_s=60)
                    if out.returncode == 0:
                        payload = _extract_json((out.stdout or "") + "\n" + (out.stderr or ""))
                        if isinstance(payload, dict) and payload.get("found") is True:
                            found = True
                            break
                        last_msg = (out.stdout or out.stderr or "").strip()
                    else:
                        last_msg = (out.stderr or out.stdout or "").strip()

                    time.sleep(8.0)

                if found:
                    self.after(0, lambda: self._gas_status_var.set("Apps Script: mirror OK"))

                    def _done() -> None:
                        cleanup = messagebox.askyesno(
                            "Mirror OK",
                            f"Drive mirroring looks OK — found {probe_name} in the Drive root folder.\n\n"
                            "Clean up the probe file (trash it in Drive and delete locally)?",
                        )
                        if cleanup:
                            try:
                                # Trash in Drive
                                _ = self._gas_clasp_run(
                                    ["run", "evidexMirrorProbeCleanup", "--params", params],
                                    cwd=proj,
                                    timeout_s=60,
                                )
                            except Exception:
                                pass
                            try:
                                probe_path.unlink(missing_ok=True)  # type: ignore[call-arg]
                            except TypeError:
                                try:
                                    if probe_path.exists():
                                        probe_path.unlink()
                                except Exception:
                                    pass

                    self.after(0, _done)
                else:
                    self.after(0, lambda: self._gas_status_var.set("Apps Script: mirror not confirmed"))
                    msg = (
                        f"Created local probe file:\n{probe_path}\n\n"
                        "Could not confirm it appeared in Drive within ~2 minutes.\n\n"
                        "Common causes:\n"
                        "- Drive for Desktop not set to Mirror that folder\n"
                        "- Wrong Drive folder mirrored vs Watch root\n"
                        "- Drive sync paused/offline\n\n"
                        "Tip: Open the Drive folder in browser and look for the probe file.\n"
                    )
                    self.after(0, lambda m=msg: messagebox.showwarning("Mirror not confirmed", m))
            except Exception as e:
                self.after(0, lambda: self._gas_status_var.set("Apps Script: mirror verify error"))
                self.after(0, lambda: messagebox.showerror("Verify mirror", f"Mirror verification failed: {e}"))

        threading.Thread(target=_run, daemon=True).start()

    def _gas_open_editor(self) -> None:
        proj, err = self._gas_require_config()
        if proj is None:
            messagebox.showinfo("Apps Script not configured", err)
            return

        def _run() -> None:
            try:
                # clasp v3 uses `open-script`; older versions used `open`.
                tried: list[str] = []

                def _try(cmd_args: list[str]) -> subprocess.CompletedProcess[str]:
                    tried.append(" ".join(cmd_args))
                    return subprocess.run(cmd_args, cwd=str(proj), capture_output=True, text=True, timeout=60)

                out = _try([self._gas_cmd(), "open-script"])
                if out.returncode != 0:
                    out2 = _try([self._gas_cmd(), "open"])  # fallback
                    if out2.returncode == 0:
                        self.after(0, lambda: self._gas_status_var.set("Apps Script: opened editor"))
                        return
                    out = out2

                if out.returncode == 0:
                    self.after(0, lambda: self._gas_status_var.set("Apps Script: opened editor"))
                    return

                # Final fallback: open editor URL directly using scriptId from .clasp.json.
                script_id = ""
                try:
                    clasp_json = proj / ".clasp.json"
                    if clasp_json.exists():
                        data = json.loads(clasp_json.read_text(encoding="utf-8"))
                        script_id = str((data or {}).get("scriptId") or "").strip()
                except Exception:
                    script_id = ""

                if script_id:
                    url = f"https://script.google.com/home/projects/{script_id}/edit"
                    self.after(0, lambda u=url: _open_url(u))
                    self.after(0, lambda: self._gas_status_var.set("Apps Script: opened editor (URL fallback)"))
                    return

                msg = (out.stderr or out.stdout or "(no output)").strip()
                extra = ("\nTried: " + ", ".join(tried)) if tried else ""
                self.after(0, lambda m=(msg + extra): self._gas_status_var.set(f"Apps Script: open failed: {m[:220]}"))
            except Exception as e:
                self.after(0, lambda: self._gas_status_var.set(f"Apps Script: open error ({type(e).__name__})"))

        threading.Thread(target=_run, daemon=True).start()

    def _gas_login_status(self) -> None:
        """Show which Google account clasp is authenticated as."""

        # Works even without a project dir, but having one avoids edge cases.
        proj = self._gas_project_dir() or _repo_root()
        self._gas_status_var.set("Apps Script: checking clasp account…")

        def _run() -> None:
            try:
                # Determine whether this clasp supports `login --status`.
                try:
                    help_out = self._gas_clasp_run(["login", "--help"], cwd=proj, timeout_s=10)
                    help_text = ((help_out.stdout or "") + "\n" + (help_out.stderr or "")).lower()
                    supports_status = "--status" in help_text
                except Exception:
                    supports_status = False

                if supports_status:
                    try:
                        out = self._gas_clasp_run(["login", "--status"], cwd=proj, timeout_s=30)
                    except FileNotFoundError:
                        self.after(0, lambda: self._gas_status_var.set("Apps Script: clasp not found (install @google/clasp)"))
                        self.after(0, lambda: messagebox.showinfo("clasp account", "clasp not found. Install Node.js + `npm i -g @google/clasp`, then run `clasp login`."))
                        return

                    text = ((out.stdout or "") + "\n" + (out.stderr or "")).strip()
                    if out.returncode == 0:
                        self.after(0, lambda: self._gas_status_var.set("Apps Script: clasp account OK"))
                        self.after(0, lambda t=text: messagebox.showinfo("clasp account", t or "(no output)"))
                        return

                # Default path: inspect ~/.clasprc.json to determine whether a token exists.
                rc_path = Path.home() / ".clasprc.json"
                if not rc_path.exists():
                    msg = (
                        "No clasp token file was found (~/.clasprc.json).\n\n"
                        "Next step: run `clasp login` (or the EVIDEX button) to authenticate."
                    )
                    self.after(0, lambda: self._gas_status_var.set("Apps Script: clasp not logged in"))
                    self.after(0, lambda m=msg: messagebox.showwarning("clasp account", m))
                    return

                # Do not display tokens. Only show whether a token exists, and if an id_token contains an email.
                data = json.loads(rc_path.read_text(encoding="utf-8"))
                tokens = (data or {}).get("tokens") or {}

                def _jwt_email(id_token: str) -> str:
                    try:
                        parts = (id_token or "").split(".")
                        if len(parts) < 2:
                            return ""
                        payload_b64 = parts[1]
                        pad = "=" * ((4 - len(payload_b64) % 4) % 4)
                        payload = base64.urlsafe_b64decode((payload_b64 + pad).encode("utf-8"))
                        obj = json.loads(payload.decode("utf-8", errors="ignore"))
                        return str(obj.get("email") or "").strip()
                    except Exception:
                        return ""

                found_profile = ""
                found_email = ""
                for name, tok in tokens.items():
                    if not isinstance(tok, dict):
                        continue
                    if tok.get("refresh_token") or tok.get("access_token"):
                        found_profile = str(name)
                        found_email = _jwt_email(str(tok.get("id_token") or ""))
                        break

                if found_profile:
                    msg_lines = ["clasp appears to be logged in (token file found)."]
                    if found_email:
                        msg_lines.append(f"Account (from token): {found_email}")
                    else:
                        msg_lines.append("Account email: (not available from token)")
                        msg_lines.append("Tip: open https://script.google.com/home and check the avatar in the top-right.")
                    msg_lines.append("")
                    msg_lines.append(f"Token file: {rc_path}")
                    msg_lines.append("")
                    msg_lines.append("To switch accounts: run `clasp logout` then `clasp login`.")
                    msg = "\n".join(msg_lines)
                    self.after(0, lambda: self._gas_status_var.set("Apps Script: clasp login present"))
                    self.after(0, lambda m=msg: messagebox.showinfo("clasp account", m))
                    return

                msg = (
                    "Found ~/.clasprc.json but no usable token entry was detected.\n\n"
                    "Next step: run `clasp login` to authenticate."
                )
                self.after(0, lambda: self._gas_status_var.set("Apps Script: clasp account unknown"))
                self.after(0, lambda m=msg: messagebox.showwarning("clasp account", m))
            except Exception as e:
                self.after(0, lambda: self._gas_status_var.set("Apps Script: clasp account error"))
                self.after(0, lambda: messagebox.showerror("clasp account", f"Could not check clasp account: {e}"))

        threading.Thread(target=_run, daemon=True).start()

    def _gas_push(self) -> None:
        proj, err = self._gas_require_config()
        if proj is None:
            messagebox.showinfo("Apps Script not configured", err)
            return

        self._gas_status_var.set("Apps Script: pushing…")

        def _run() -> None:
            try:
                cmd = [self._gas_cmd(), "push", "-f"]
                out = subprocess.run(cmd, cwd=str(proj), capture_output=True, text=True, timeout=60 * 5)
                if out.returncode == 0:
                    self.after(0, lambda: self._gas_status_var.set("Apps Script: push OK"))
                else:
                    msg = (out.stderr or out.stdout or "(no output)").strip()
                    self.after(0, lambda m=msg: self._gas_status_var.set(f"Apps Script: push failed: {m[:220]}"))
            except FileNotFoundError:
                self.after(0, lambda: self._gas_status_var.set("Apps Script: clasp not found (install @google/clasp)") )
            except Exception as e:
                self.after(0, lambda: self._gas_status_var.set(f"Apps Script: push error ({type(e).__name__})"))

        threading.Thread(target=_run, daemon=True).start()

    def _gas_run(self, fn_name: str) -> None:
        proj, err = self._gas_require_config()
        if proj is None:
            messagebox.showinfo("Apps Script not configured", err)
            return

        name = (fn_name or "").strip()
        if not name:
            return

        self._gas_status_var.set(f"Apps Script: running {name}()…")

        def _run() -> None:
            try:
                out = self._gas_clasp_run(["run", name], cwd=proj, timeout_s=60 * 8)
                text = ((out.stdout or "") + "\n" + (out.stderr or "")).strip()
                if out.returncode == 0:
                    self.after(0, lambda: self._gas_status_var.set(f"Apps Script: ran {name}()"))
                    self.after(0, lambda t=text: messagebox.showinfo("Apps Script", t[-1600:] or "(no output)"))
                    return

                msg = (out.stderr or out.stdout or "(no output)").strip()

                def _warn() -> None:
                    ml = msg.lower()
                    if "unable to run script function" in ml and "permission" in ml:
                        help_msg = (
                            "Apps Script is not authorized to run via the API yet.\n\n"
                            "Fix (one-time):\n"
                            "1) Click 'Open editor' in the Google tab\n"
                            "2) In the Apps Script editor, select any function (e.g. evidexShowConfig) and click Run\n"
                            "3) Approve the permission prompts\n\n"
                            "Then retry this button."
                        )
                        messagebox.showwarning("Authorize Apps Script", help_msg)
                    else:
                        messagebox.showwarning("Apps Script", text[-1600:] or "Run failed.")

                self.after(0, lambda m=msg: self._gas_status_var.set(f"Apps Script: run failed: {m[:220]}"))
                self.after(0, _warn)
            except FileNotFoundError:
                self.after(0, lambda: self._gas_status_var.set("Apps Script: clasp not found (install @google/clasp)"))
            except Exception as e:
                self.after(0, lambda: self._gas_status_var.set(f"Apps Script: run error ({type(e).__name__})"))

        threading.Thread(target=_run, daemon=True).start()

    def _open_settings_file(self) -> None:
        path = default_env_file_path()
        try:
            if not path.exists():
                save_env_file(path, {})
            _open_path(path)
        except Exception as e:
            messagebox.showerror("Failed", f"Could not open settings: {e}")

    def _try_load_logo(self, *, target_width: int = 72):
        # Returns a Tk PhotoImage or None
        try:
            repo_root = Path(__file__).resolve().parents[2]
        except Exception:
            repo_root = Path.cwd()
        raw = (os.getenv("EVIDEX_LOGO_PATH") or "").strip()
        logo_path = Path(raw).expanduser() if raw else (repo_root / "assets" / "evidex_logo.png")
        try:
            if not logo_path.exists():
                return None
            img = tk.PhotoImage(file=str(logo_path))
            # Subsample large images to a reasonable size for the header.
            try:
                w = max(1, int(img.width()))
                factor = max(1, w // max(24, int(target_width)))
                if factor > 1:
                    img = img.subsample(factor, factor)
            except Exception:
                pass
            return img
        except Exception:
            return None

    def _dashboard_window_seconds(self) -> int:
        v = str(self._dash_window_var.get() or "7d").strip().lower()
        if v.startswith("1"):
            return 24 * 3600
        if v.startswith("30"):
            return 30 * 24 * 3600
        return 7 * 24 * 3600

    def _refresh_dashboard(self) -> None:
        root = self._watch_root_path()
        now = time.time()
        since = now - float(self._dashboard_window_seconds())

        require_payment = _env_truthy("REQUIRE_PAYMENT", "0")
        unit_cost = _env_float("UNIT_COST", 0.0)

        # Compute counts + simple time series
        total = 0
        incoming = 0
        processing = 0
        done = 0
        failed = 0

        ready_to_email = 0
        emailed = 0

        paid_count = 0
        revenue_paid = 0.0
        profit_paid = 0.0

        daily_requests: dict[str, int] = {}
        daily_completed: dict[str, int] = {}
        daily_paid: dict[str, float] = {}

        def _day_key(ts: float) -> str:
            return time.strftime("%Y-%m-%d", time.localtime(ts))

        # Lazy imports to avoid any startup cost until dashboard renders
        load_intake = None
        resolve_pricing = None
        try:
            from .pack import load_intake as _li
            from .ops import resolve_pricing_from_intake as _rp

            load_intake = _li
            resolve_pricing = _rp
        except Exception:
            load_intake = None
            resolve_pricing = None

        for stage, job_dir in _iter_job_dirs(root):
            total += 1
            if stage == "incoming":
                incoming += 1
            elif stage == "processing":
                processing += 1
            elif stage == "done":
                done += 1
            elif stage == "failed":
                failed += 1

            # Email readiness (Drive emailer writes SENT.txt)
            has_deliverable = (job_dir / "DELIVERABLE.zip").exists()
            has_contact = (job_dir / "CONTACT_EMAIL.txt").exists()
            has_sent = (job_dir / "SENT.txt").exists()
            has_paid = (job_dir / "PAID.txt").exists()

            if has_sent:
                emailed += 1
            if has_deliverable and has_contact and (not has_sent):
                if (not require_payment) or has_paid:
                    ready_to_email += 1

            # Requests created trend: use intake.yaml mtime if present
            intake_path = job_dir / "intake.yaml"
            try:
                created_ts = intake_path.stat().st_mtime if intake_path.exists() else job_dir.stat().st_mtime
            except OSError:
                created_ts = None
            if created_ts and created_ts >= since:
                daily_requests[_day_key(created_ts)] = daily_requests.get(_day_key(created_ts), 0) + 1

            # Completed trend: use deliverable mtime
            if has_deliverable:
                try:
                    comp_ts = (job_dir / "DELIVERABLE.zip").stat().st_mtime
                except OSError:
                    comp_ts = None
                if comp_ts and comp_ts >= since:
                    daily_completed[_day_key(comp_ts)] = daily_completed.get(_day_key(comp_ts), 0) + 1

            # Paid revenue trend: use PAID.txt mtime and intake pricing
            if has_paid:
                paid_count += 1
                try:
                    paid_ts = (job_dir / "PAID.txt").stat().st_mtime
                except OSError:
                    paid_ts = None

                job_total = 0.0
                job_qty = 1
                if load_intake and resolve_pricing and intake_path.exists():
                    try:
                        intake = load_intake(intake_path)
                        qty, unit_price, _currency, _service_name = resolve_pricing(intake)
                        job_qty = int(qty)
                        job_total = float(unit_price) * float(qty)
                    except Exception:
                        job_total = 0.0
                revenue_paid += float(job_total)
                profit_paid += float(job_total) - (float(unit_cost) * float(job_qty))

                if paid_ts and paid_ts >= since:
                    k = _day_key(paid_ts)
                    daily_paid[k] = float(daily_paid.get(k, 0.0)) + float(job_total)

        summary_lines: list[str] = []
        summary_lines.append(f"Jobs: {total}  |  Incoming: {incoming}  Processing: {processing}  Done: {done}  Failed: {failed}")
        summary_lines.append(f"Email: ready-to-send: {ready_to_email}  |  sent: {emailed}")
        if unit_cost > 0:
            summary_lines.append(f"Paid jobs: {paid_count}  |  Revenue (gross): {revenue_paid:.2f}  |  Est. profit: {profit_paid:.2f}")
        else:
            summary_lines.append(f"Paid jobs: {paid_count}  |  Revenue (gross): {revenue_paid:.2f}")
        self._dash_summary_var.set("\n".join(summary_lines))

        # Draw charts
        self._draw_requests_chart(daily_requests, daily_completed, since)
        self._draw_payments_chart(daily_paid, since)

    def _draw_requests_chart(self, requests: dict[str, int], completed: dict[str, int], since_ts: float) -> None:
        canvas = self._chart_requests
        canvas.delete("all")
        canvas.create_text(10, 10, anchor="nw", text="Requests vs completed", fill="#233043", font=("Segoe UI", 10, "bold"))

        buckets, fmt = self._enumerate_time_buckets(since_ts)
        series = [(fmt(k), int(requests.get(k, 0))) for k in buckets]
        series2 = [(fmt(k), int(completed.get(k, 0))) for k in buckets]
        self._draw_dual_bars(canvas, series, series2, color1="#2d7ff9", color2="#22a06b")

    def _draw_payments_chart(self, paid: dict[str, float], since_ts: float) -> None:
        canvas = self._chart_payments
        canvas.delete("all")
        canvas.create_text(10, 10, anchor="nw", text="Paid revenue (gross)", fill="#233043", font=("Segoe UI", 10, "bold"))

        buckets, fmt = self._enumerate_time_buckets(since_ts)
        series = [(fmt(k), float(paid.get(k, 0.0))) for k in buckets]
        self._draw_bars(canvas, series, color="#6f42c1")

    def _enumerate_time_buckets(self, since_ts: float) -> tuple[list[str], Callable[[str], str]]:
        """Return bucket keys and a label formatter.

        - For windows <= ~70 days: buckets are days (YYYY-MM-DD) labeled as 'Jan 22'.
        - For longer windows: buckets are months (YYYY-MM) labeled as 'Jan', 'Feb', ...
        """

        start_dt = datetime.fromtimestamp(since_ts)
        start = start_dt.date()
        end = datetime.now().date()
        days = max(0, (end - start).days)

        if days <= 70:
            out: list[str] = []
            cur = start
            while cur <= end:
                out.append(cur.isoformat())
                cur = cur + timedelta(days=1)

            def _fmt_day(k: str) -> str:
                try:
                    dt = datetime.strptime(k, "%Y-%m-%d")
                    return dt.strftime("%b %d")
                except Exception:
                    return k

            return out, _fmt_day

        # Month buckets
        out_m: list[str] = []
        cur = date(start.year, start.month, 1)
        end_m = date(end.year, end.month, 1)
        while cur <= end_m:
            out_m.append(f"{cur.year:04d}-{cur.month:02d}")
            if cur.month == 12:
                cur = date(cur.year + 1, 1, 1)
            else:
                cur = date(cur.year, cur.month + 1, 1)

        def _fmt_month(k: str) -> str:
            try:
                dt = datetime.strptime(k, "%Y-%m")
                return dt.strftime("%b")
            except Exception:
                return k

        return out_m, _fmt_month

    def _draw_bars(self, canvas: tk.Canvas, series: list[tuple[str, float]], *, color: str) -> None:
        w = max(1, int(canvas.winfo_width() or 640))
        h = max(1, int(canvas.winfo_height() or 260))
        pad_l = 40
        pad_r = 10
        pad_t = 30
        pad_b = 30
        chart_w = max(1, w - pad_l - pad_r)
        chart_h = max(1, h - pad_t - pad_b)

        vals = [v for _k, v in series]
        vmax = max(vals) if vals else 0.0
        vmax = float(vmax) if vmax > 0 else 1.0

        n = max(1, len(series))
        bw = max(1, int(chart_w / n))

        # axes
        canvas.create_line(pad_l, pad_t, pad_l, pad_t + chart_h, fill="#c9d2dd")
        canvas.create_line(pad_l, pad_t + chart_h, pad_l + chart_w, pad_t + chart_h, fill="#c9d2dd")

        for i, (k, v) in enumerate(series):
            x0 = pad_l + i * bw + 2
            x1 = min(pad_l + (i + 1) * bw - 2, pad_l + chart_w)
            bar_h = int((float(v) / vmax) * chart_h)
            y1 = pad_t + chart_h
            y0 = y1 - bar_h
            canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")

            # Value label above each bar (only when legible).
            if n <= 14 and float(v) > 0:
                try:
                    label = f"{int(v)}" if float(v).is_integer() else f"{v:.1f}"
                except Exception:
                    label = str(v)
                canvas.create_text((x0 + x1) / 2, max(pad_t + 10, y0 - 10), text=label, fill="#233043", font=("Segoe UI", 8))

            if n <= 8 or i in {0, n - 1} or (n <= 32 and i % 7 == 0):
                canvas.create_text((x0 + x1) / 2, pad_t + chart_h + 12, text=str(k), fill="#5a6675", font=("Segoe UI", 8))

        canvas.create_text(8, pad_t + chart_h, anchor="sw", text=str(int(vmax)), fill="#5a6675", font=("Segoe UI", 8))
        canvas.create_text(8, pad_t, anchor="nw", text="0", fill="#5a6675", font=("Segoe UI", 8))

    def _draw_dual_bars(self, canvas: tk.Canvas, s1: list[tuple[str, int]], s2: list[tuple[str, int]], *, color1: str, color2: str) -> None:
        # Two series stacked side-by-side for each day.
        w = max(1, int(canvas.winfo_width() or 640))
        h = max(1, int(canvas.winfo_height() or 260))
        pad_l = 40
        pad_r = 10
        pad_t = 30
        pad_b = 30
        chart_w = max(1, w - pad_l - pad_r)
        chart_h = max(1, h - pad_t - pad_b)

        vals = [v for _k, v in s1] + [v for _k, v in s2]
        vmax = max(vals) if vals else 0
        vmax = int(vmax) if vmax > 0 else 1

        n = max(1, len(s1))
        bw = max(2, int(chart_w / n))
        half = max(1, int((bw - 2) / 2))

        canvas.create_line(pad_l, pad_t, pad_l, pad_t + chart_h, fill="#c9d2dd")
        canvas.create_line(pad_l, pad_t + chart_h, pad_l + chart_w, pad_t + chart_h, fill="#c9d2dd")

        for i in range(n):
            k = s1[i][0]
            v1 = float(s1[i][1])
            v2 = float(s2[i][1])

            x_base = pad_l + i * bw + 2
            x0a = x_base
            x1a = min(x_base + half, pad_l + chart_w)
            x0b = min(x_base + half + 2, pad_l + chart_w)
            x1b = min(x_base + 2 * half + 2, pad_l + chart_w)

            y1 = pad_t + chart_h
            y0a = y1 - int((v1 / vmax) * chart_h)
            y0b = y1 - int((v2 / vmax) * chart_h)
            canvas.create_rectangle(x0a, y0a, x1a, y1, fill=color1, outline="")
            canvas.create_rectangle(x0b, y0b, x1b, y1, fill=color2, outline="")

            # Value labels (only when legible).
            if n <= 14:
                if v1 > 0:
                    canvas.create_text((x0a + x1a) / 2, max(pad_t + 10, y0a - 10), text=str(int(v1)), fill="#233043", font=("Segoe UI", 8))
                if v2 > 0:
                    canvas.create_text((x0b + x1b) / 2, max(pad_t + 10, y0b - 10), text=str(int(v2)), fill="#233043", font=("Segoe UI", 8))

            if n <= 8 or i in {0, n - 1} or (n <= 32 and i % 7 == 0):
                canvas.create_text(x_base + half, pad_t + chart_h + 12, text=str(k), fill="#5a6675", font=("Segoe UI", 8))

        # Legend
        canvas.create_rectangle(pad_l + 10, pad_t + 6, pad_l + 20, pad_t + 16, fill=color1, outline="")
        canvas.create_text(pad_l + 24, pad_t + 11, anchor="w", text="requests", fill="#5a6675", font=("Segoe UI", 8))
        canvas.create_rectangle(pad_l + 90, pad_t + 6, pad_l + 100, pad_t + 16, fill=color2, outline="")
        canvas.create_text(pad_l + 104, pad_t + 11, anchor="w", text="completed", fill="#5a6675", font=("Segoe UI", 8))

    def _dashboard_tick(self) -> None:
        try:
            # Only redraw periodically to keep it light.
            self._refresh_dashboard()
        finally:
            self.after(12000, self._dashboard_tick)

    def _kick_imap_refresh(self) -> None:
        # Optionally persist IMAP settings.
        os.environ["EVIDEX_EMAIL_IMAP_USER"] = self._imap_user_var.get().strip()
        os.environ["EVIDEX_EMAIL_IMAP_PASSWORD"] = self._imap_pass_var.get().strip().replace(" ", "")
        os.environ["EVIDEX_EMAIL_IMAP_HOST"] = self._imap_host_var.get().strip()
        os.environ["EVIDEX_EMAIL_IMAP_PORT"] = self._imap_port_var.get().strip()
        if self._imap_remember_var.get():
            try:
                save_env_file(default_env_file_path(), {
                    "EVIDEX_EMAIL_IMAP_HOST": self._imap_host_var.get().strip(),
                    "EVIDEX_EMAIL_IMAP_PORT": self._imap_port_var.get().strip(),
                    "EVIDEX_EMAIL_IMAP_USER": self._imap_user_var.get().strip(),
                    "EVIDEX_EMAIL_IMAP_PASSWORD": self._imap_pass_var.get().strip().replace(" ", ""),
                })
            except Exception:
                pass
        # Trigger an immediate refresh in the background thread loop.
        self._imap_status_var.set("Inbox: connecting…")
        try:
            self._imap_refresh.set()
        except Exception:
            pass

    def _start_imap_monitor(self) -> None:
        # Background loop: poll unread count if configured.
        def _loop() -> None:
            while not self._imap_stop.is_set():
                try:
                    host = (self._imap_host_var.get() or "").strip() or "imap.gmail.com"
                    port_raw = (self._imap_port_var.get() or "").strip() or "993"
                    try:
                        port = int(port_raw)
                    except ValueError:
                        port = 993
                    user = (self._imap_user_var.get() or "").strip()
                    pwd = (self._imap_pass_var.get() or "").strip()
                    # Google App Passwords are shown grouped with spaces; remove them.
                    pwd = pwd.replace(" ", "")
                    if not user or not pwd:
                        self.after(0, lambda: self._imap_unread_var.set("Unread: (not configured)"))
                        self.after(0, lambda: self._imap_status_var.set("Inbox: set IMAP host/user + app password to monitor"))
                        time.sleep(8.0)
                        continue

                    ctx = ssl.create_default_context()
                    timeout_sec = 12.0
                    old_timeout = socket.getdefaulttimeout()
                    try:
                        # Python 3.9+ supports `timeout=`.
                        m = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=timeout_sec)
                    except TypeError:
                        socket.setdefaulttimeout(timeout_sec)
                        m = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
                    finally:
                        socket.setdefaulttimeout(old_timeout)
                    m.login(user, pwd)
                    m.select("INBOX")
                    typ, data = m.search(None, "UNSEEN")
                    unread = 0
                    if typ == "OK" and data and data[0]:
                        unread = len(str(data[0], "utf-8").split())
                    m.logout()
                    self.after(0, lambda u=unread: self._imap_unread_var.set(f"Unread: {u}"))
                    self.after(0, lambda: self._imap_status_var.set("Inbox: connected"))
                except Exception as e:
                    msg = (str(e) or "").strip()
                    if msg:
                        self.after(0, lambda m=msg: self._imap_status_var.set(f"Inbox: error ({type(e).__name__}): {m}"))
                    else:
                        self.after(0, lambda: self._imap_status_var.set(f"Inbox: error ({type(e).__name__})"))

                # Poll interval
                for _ in range(20):
                    if self._imap_stop.is_set():
                        break
                    # Allow the Connect button to trigger an immediate refresh.
                    if self._imap_refresh.wait(timeout=1.0):
                        try:
                            self._imap_refresh.clear()
                        except Exception:
                            pass
                        break

        threading.Thread(target=_loop, daemon=True).start()

    def _show_text_modal(self, title: str, text: str) -> None:
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("860x520")

        outer = ttk.Frame(win)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        t = tk.Text(outer, wrap="word")
        y = ttk.Scrollbar(outer, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=y.set)
        y.pack(side="right", fill="y")
        t.pack(side="left", fill="both", expand=True)
        t.insert("end", text or "(no output)\n")
        t.configure(state="disabled")

        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 10))

    def _cleanup_jobs_trash(self) -> None:
        root = self._watch_root_path()
        if not root.exists():
            messagebox.showerror("Cleanup", f"Watch root not found:\n{root}")
            return

        ok = messagebox.askyesno(
            "Cleanup jobs",
            "This will move duplicate/test jobs into a timestamped _trash folder under your watch root.\n\nContinue?",
        )
        if not ok:
            return

        try:
            repo_root = Path(__file__).resolve().parents[2]
            script = repo_root / "tools" / "cleanup_jobs.ps1"
        except Exception:
            script = None

        if not script or not script.exists():
            messagebox.showerror("Cleanup", "Could not find tools/cleanup_jobs.ps1")
            return

        def _run() -> None:
            try:
                cmd = [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-Root",
                    str(root),
                    "-Apply",
                ]
                p = subprocess.run(cmd, capture_output=True, text=True)
                out = (p.stdout or "")
                err = (p.stderr or "")
                combined = (out + ("\n" + err if err else "")).strip()
                if p.returncode != 0:
                    raise RuntimeError(combined or f"cleanup exited with code {p.returncode}")
                self.after(0, lambda: self._show_text_modal("Cleanup complete", combined))
                self.after(0, self._refresh_jobs)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Cleanup failed", str(e)))

        threading.Thread(target=_run, daemon=True).start()

    def _process_selected_now(self) -> None:
        job_dir = self._get_selected_job()
        if not job_dir:
            return

        cfg = self._cfg()

        def _run() -> None:
            try:
                run_job_once(job_dir=job_dir, cfg=cfg)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Processing failed", str(e)))
            finally:
                self.after(0, self._refresh_jobs)

        threading.Thread(target=_run, daemon=True).start()

    def _start_watcher(self) -> None:
        if self._watch_thread and self._watch_thread.is_alive():
            return

        # Common Drive-mirroring pitfall: user sets watch root to the parent folder,
        # but the mirrored Drive folder (e.g. EvidenceEngine/) sits one level below.
        try:
            root = self._watch_root_path()
            direct_incoming = root / "incoming"
            nested_root = root / "EvidenceEngine"
            nested_incoming = nested_root / "incoming"
            if direct_incoming.exists() and nested_incoming.exists():
                has_direct_jobs = any(p.is_dir() for p in direct_incoming.iterdir())
                has_nested_jobs = any(p.is_dir() for p in nested_incoming.iterdir())
                if (not has_direct_jobs) and has_nested_jobs:
                    msg = (
                        "Your mirrored jobs are under a nested folder:\n\n"
                        f"- Current watch root: {root}\n"
                        f"- Jobs found in: {nested_incoming}\n\n"
                        "The watcher only processes jobs from <watch_root>/incoming.\n\n"
                        "Fix: set Watch root to the mirrored Drive folder itself (EvidenceEngine), e.g.:\n"
                        f"{nested_root}"
                    )
                    messagebox.showwarning("Watcher: wrong root", msg)
        except Exception:
            pass

        self._watch_stop.clear()
        cfg = self._cfg()

        def _run() -> None:
            self.after(0, lambda: self._watch_status.set("Watcher: running"))
            try:
                watch(cfg, stop_event=self._watch_stop)
            except Exception as e:
                msg = str(e)
                self.after(0, lambda m=msg: self._watcher_note.set(f"Watcher stopped: {m}"))
                if "Watcher already running" in msg or "watcher lock" in msg.lower():
                    self.after(0, lambda m=msg: messagebox.showwarning("Watcher already running", m))
            finally:
                self.after(0, lambda: self._watch_status.set("Watcher: stopped"))

        self._watch_thread = threading.Thread(target=_run, daemon=True)
        self._watch_thread.start()

    def _stop_watcher(self) -> None:
        self._watch_stop.set()

    def _log_marketing(self, text: str) -> None:
        self._mk_log.configure(state="normal")
        self._mk_log.insert("end", text + "\n")
        self._mk_log.see("end")
        self._mk_log.configure(state="disabled")

    def _run_ad_generator(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        script = repo_root / "scripts" / "marketing" / "generate_week1_ads.py"
        py = Path(sys.executable)  # type: ignore[name-defined]

        cmd = [str(py), str(script), "--out", str(repo_root / "output" / "marketing")]
        if self._refine_ads_var.get():
            cmd.append("--refine")
        if self._images_ads_var.get():
            cmd.append("--images")

        self._log_marketing("Running: " + " ".join(cmd))

        def _run() -> None:
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_root))
                out = (proc.stdout or "") + (proc.stderr or "")
                self.after(0, lambda: self._log_marketing(out.strip() or "(no output)"))
            except Exception as e:
                self.after(0, lambda: self._log_marketing(f"Failed: {e}"))

        threading.Thread(target=_run, daemon=True).start()

    def _open_marketing_output(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        out_dir = repo_root / "output" / "marketing"
        out_dir.mkdir(parents=True, exist_ok=True)
        _open_path(out_dir)

    def _open_marketing_templates(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        try:
            _open_path(repo_root / "marketing")
        except Exception:
            pass

    def _mk_generate_rotation(self, *, force_days: int | None = None) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        out_dir = repo_root / "output" / "marketing"

        try:
            start = datetime.strptime(self._mk_start_var.get().strip(), "%Y-%m-%d").date()
        except Exception:
            messagebox.showerror("Marketing", "Start date must be YYYY-MM-DD")
            return

        try:
            days = int(force_days or int(self._mk_days_var.get().strip() or "10"))
        except Exception:
            days = 10
        days = max(1, min(90, days))

        # Persist the links into env for generator reuse (optional).
        os.environ["EVIDEX_LINK_LOCANTO"] = self._mk_locanto_var.get().strip()
        os.environ["EVIDEX_LINK_MYADZ"] = self._mk_mya_var.get().strip()
        os.environ["EVIDEX_LINK_LINKEDIN"] = self._mk_li_var.get().strip()

        def _run() -> None:
            try:
                mode = (self._mk_mode_var.get() or "one_per_stream_per_day").strip() or "one_per_stream_per_day"
                items = generate_rotation(start=start, days=days, refine=bool(self._refine_ads_var.get()), mode=mode)
                stem = f"rotation_{start.strftime('%Y%m%d')}_{days}d_{'3pd' if mode != 'all_platforms' else 'all'}"
                json_path, csv_path = write_rotation_outputs(items=items, out_dir=out_dir, stem=stem)
                self._mk_last_export = (json_path, csv_path)
                self.after(0, lambda: self._mk_load_items(items))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Marketing", str(e)))

        threading.Thread(target=_run, daemon=True).start()

    def _mk_load_items(self, items: list) -> None:
        # items is a list[ScheduledAd], but keep it flexible.
        self._mk_items = [
            {
                "day": getattr(x, "day", ""),
                "stream": getattr(x, "stream", ""),
                "stream_name": getattr(x, "stream_name", ""),
                "moment": getattr(x, "moment", ""),
                "core_message": getattr(x, "core_message", ""),
                "platform": getattr(x, "platform", ""),
                "persona": getattr(x, "persona", ""),
                "template_image": getattr(x, "template_image", ""),
                "headline": getattr(x, "headline", ""),
                "tagline": getattr(x, "tagline", ""),
                "primary_text": getattr(x, "primary_text", ""),
                "cta": getattr(x, "cta", ""),
                "links": getattr(x, "links", {}) or {},
            }
            for x in items
        ]

        for iid in self._mk_tree.get_children():
            self._mk_tree.delete(iid)

        for idx, it in enumerate(self._mk_items):
            img_name = Path(it.get("template_image") or "").name

            # Precompute live columns
            day = str(it.get("day") or "")
            stream = str(it.get("stream") or "")
            platform = str(it.get("platform") or "")
            k = make_key(day, stream, platform)
            t = self._mk_tracker.get(k)
            status = "planned"
            views = ""
            reminder = ""
            tags: tuple[str, ...] = ()
            if t and t.status == "live":
                status = "live"
                if t.views is not None:
                    views = str(t.views)
                remaining = (float(t.created_ts or time.time()) + 24 * 3600) - time.time()
                if remaining <= 0:
                    reminder = "DUE"
                    tags = ("remind",)
                else:
                    hrs = int(remaining // 3600)
                    mins = int((remaining % 3600) // 60)
                    reminder = f"{hrs:02d}:{mins:02d}"
                    tags = ("live",)

            self._mk_tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    it.get("day", ""),
                    it.get("stream", ""),
                    it.get("moment", ""),
                    it.get("platform", ""),
                    status,
                    views,
                    reminder,
                    it.get("persona", ""),
                    img_name,
                    it.get("headline", ""),
                ),
                tags=tags,
            )

        self._mk_update_preview()

    def _mk_selected_item(self) -> dict | None:
        sel = self._mk_tree.selection()
        if not sel:
            return None
        try:
            idx = int(sel[0])
        except Exception:
            return None
        if idx < 0 or idx >= len(self._mk_items):
            return None
        return self._mk_items[idx]

    def _mk_update_preview(self) -> None:
        it = self._mk_selected_item()
        self._mk_preview.configure(state="normal")
        self._mk_preview.delete("1.0", "end")
        if not it:
            self._mk_preview.insert("end", "Select a row to preview.\n")
        else:
            links = it.get("links") or {}
            text = (
                f"{it.get('headline','').strip()}\n"
                f"{it.get('tagline','').strip()}\n\n"
                f"{it.get('primary_text','').strip()}\n\n"
                f"CTA: {it.get('cta','').strip()}\n\n"
                f"Links: LinkedIn {links.get('linkedin','')} | Locanto {links.get('locanto','')} | MyAdz {links.get('myadz','')}\n"
                f"Stream: {it.get('stream','')} — {it.get('stream_name','')}\n"
                f"Moment: {it.get('moment','')}\n"
                f"Core message: {it.get('core_message','')}\n"
                f"Platform: {it.get('platform','')} | Persona: {it.get('persona','')} | Day: {it.get('day','')}\n"
            )
            self._mk_preview.insert("end", text)
        self._mk_preview.configure(state="disabled")

        # Sync tracking fields for selected row
        try:
            k = make_key(str(it.get("day") or ""), str(it.get("stream") or ""), str(it.get("platform") or ""))
            t = self._mk_tracker.get(k)
            if t and t.url:
                self._mk_ad_url_var.set(t.url)
                self._mk_status_var.set(f"Status: {t.status}")
                self._mk_views_var.set(f"Views: {t.views if t.views is not None else '(unknown)'}")
                if t.last_checked_ts:
                    self._mk_checked_var.set("Last checked: " + time.strftime("%Y-%m-%d %H:%M", time.localtime(t.last_checked_ts)))
                else:
                    self._mk_checked_var.set("Last checked: (never)")
            else:
                self._mk_status_var.set("Status: planned")
                self._mk_views_var.set("Views: (unknown)")
                self._mk_checked_var.set("Last checked: (never)")
        except Exception:
            pass

    def _mk_copy_selected(self) -> None:
        it = self._mk_selected_item()
        if not it:
            return
        links = it.get("links") or {}
        text = (
            f"{it.get('headline','').strip()}\n"
            f"{it.get('tagline','').strip()}\n\n"
            f"{it.get('primary_text','').strip()}\n\n"
            f"CTA: {it.get('cta','').strip()}\n\n"
            f"LinkedIn: {links.get('linkedin','')}\nLocanto: {links.get('locanto','')}\nMyAdz: {links.get('myadz','')}\n"
        )
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except Exception:
            pass

    def _mk_open_template(self) -> None:
        it = self._mk_selected_item()
        if not it:
            return
        p = Path(it.get("template_image") or "")
        if p.exists():
            _open_path(p)

    def _mk_platform_url_for_item(self, it: dict) -> str:
        platform = str(it.get("platform") or "").strip()
        if platform.lower() == "locanto":
            return self._mk_locanto_var.get().strip()
        if platform.lower() == "myadz":
            return self._mk_mya_var.get().strip()
        # Default: LinkedIn
        return self._mk_li_var.get().strip()

    def _mk_make_posting_kit(self, it: dict) -> Path:
        repo_root = Path(__file__).resolve().parents[2]
        out_root = repo_root / "output" / "marketing" / "posting_kits"
        day = str(it.get("day") or "unknown_day")
        stream = str(it.get("stream") or "X")
        platform = str(it.get("platform") or "Platform")
        kit_dir = out_root / day / f"stream_{stream}" / platform
        kit_dir.mkdir(parents=True, exist_ok=True)

        # Write copy
        links = it.get("links") or {}
        post_text = (
            f"{it.get('headline','').strip()}\n"
            f"{it.get('tagline','').strip()}\n\n"
            f"{it.get('primary_text','').strip()}\n\n"
            f"CTA: {it.get('cta','').strip()}\n\n"
            f"LinkedIn: {links.get('linkedin','')}\nLocanto: {links.get('locanto','')}\nMyAdz: {links.get('myadz','')}\n\n"
            f"Moment: {it.get('moment','')}\nCore message: {it.get('core_message','')}\n"
        )
        (kit_dir / "post.txt").write_text(post_text, encoding="utf-8")

        # Copy template image into kit (keeps posting workflow frictionless)
        src_img = Path(it.get("template_image") or "")
        if src_img.exists():
            try:
                dst = kit_dir / ("template" + src_img.suffix.lower())
                shutil.copy2(src_img, dst)
            except Exception:
                pass

        return kit_dir

    def _mk_open_posting_kit_and_platform(self) -> None:
        it = self._mk_selected_item()
        if not it:
            return
        try:
            kit = self._mk_make_posting_kit(it)
            _open_path(kit)
        except Exception:
            pass
        try:
            url = self._mk_platform_url_for_item(it)
            if url:
                webbrowser.open(url)
        except Exception:
            pass

    def _mk_mark_live(self) -> None:
        it = self._mk_selected_item()
        if not it:
            return
        url = (self._mk_ad_url_var.get() or "").strip()
        if not url:
            messagebox.showerror("Live tracking", "Paste the ad URL first.")
            return
        k = make_key(str(it.get("day") or ""), str(it.get("stream") or ""), str(it.get("platform") or ""))
        t = self._mk_tracker.get(k)
        if not t:
            t = TrackedAd(key=k, url=url, status="live", created_ts=time.time())
        t.url = url
        t.status = "live"
        self._mk_tracker[k] = t
        try:
            save_tracker(self._mk_tracker_path, self._mk_tracker)
        except Exception:
            pass
        self._mk_status_var.set("Status: live")
        try:
            self._mk_refresh_live_columns()
        except Exception:
            pass

    def _mk_check_views(self) -> None:
        it = self._mk_selected_item()
        if not it:
            return
        k = make_key(str(it.get("day") or ""), str(it.get("stream") or ""), str(it.get("platform") or ""))
        t = self._mk_tracker.get(k)
        url = (self._mk_ad_url_var.get() or "").strip()
        if not url and t:
            url = t.url
        if not url:
            messagebox.showerror("Live tracking", "Paste the ad URL first.")
            return

        def _run() -> None:
            views = try_fetch_views(url)
            now = time.time()
            tt = t or TrackedAd(key=k, url=url, status="live", created_ts=now)
            tt.url = url
            tt.status = "live"
            tt.last_checked_ts = now
            tt.views = views
            self._mk_tracker[k] = tt
            try:
                save_tracker(self._mk_tracker_path, self._mk_tracker)
            except Exception:
                pass

            def _ui() -> None:
                self._mk_status_var.set(f"Status: {tt.status}")
                self._mk_views_var.set(f"Views: {views if views is not None else '(unknown)'}")
                self._mk_checked_var.set("Last checked: " + time.strftime("%Y-%m-%d %H:%M", time.localtime(now)))

            self.after(0, _ui)

        threading.Thread(target=_run, daemon=True).start()

    def _on_close(self) -> None:
        self._stop_watcher()
        self._imap_stop.set()
        self.destroy()


def main() -> int:
    app = EvidexDesktop()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
