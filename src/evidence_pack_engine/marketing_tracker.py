from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import request


@dataclass
class TrackedAd:
    key: str  # day|stream|platform
    url: str
    status: str  # planned/live/unknown
    created_ts: float
    last_checked_ts: float | None = None
    views: int | None = None


def make_key(day: str, stream: str, platform: str) -> str:
    return f"{day}|{stream}|{platform}"


def load_tracker(path: Path) -> dict[str, TrackedAd]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    out: dict[str, TrackedAd] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if not isinstance(v, dict):
                continue
            out[k] = TrackedAd(
                key=str(k),
                url=str(v.get("url") or ""),
                status=str(v.get("status") or "unknown"),
                created_ts=float(v.get("created_ts") or time.time()),
                last_checked_ts=(float(v["last_checked_ts"]) if v.get("last_checked_ts") is not None else None),
                views=(int(v["views"]) if v.get("views") is not None else None),
            )
    return out


def save_tracker(path: Path, tracker: dict[str, TrackedAd]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    for k, t in tracker.items():
        data[k] = {
            "url": t.url,
            "status": t.status,
            "created_ts": t.created_ts,
            "last_checked_ts": t.last_checked_ts,
            "views": t.views,
        }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


_VIEW_PATTERNS = [
    re.compile(r"\b(\d{1,9})\s*(?:views|view)\b", re.IGNORECASE),
    re.compile(r"\bviews\s*[:=]\s*(\d{1,9})\b", re.IGNORECASE),
]


def try_fetch_views(url: str, *, timeout_s: float = 12.0) -> int | None:
    """Best-effort view extraction from public HTML.

    Many platforms hide views behind login or use heavy JS, so this may return None.
    """

    u = (url or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        return None

    req = request.Request(
        url=u,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )

    try:
        with request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read(512_000).decode("utf-8", errors="ignore")
    except Exception:
        return None

    # Quick heuristics
    for pat in _VIEW_PATTERNS:
        m = pat.search(body)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None

    return None
