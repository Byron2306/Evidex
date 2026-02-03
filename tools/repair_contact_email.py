from __future__ import annotations

from pathlib import Path

import yaml


def _get_email(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    client = data.get("client") or {}
    if not isinstance(client, dict):
        return ""
    for key in ("contact_email", "contactEmail", "email", "contact"):
        v = client.get(key)
        if isinstance(v, str) and "@" in v:
            return v.strip()
    return ""


def main() -> int:
    root = Path(r"C:\Users\User\Desktop\EvidenceEngine\EvidenceEngine")
    if not root.exists():
        print(f"Root not found: {root}")
        return 1

    repaired = 0
    missing: list[str] = []

    for stage in ("incoming", "processing", "done", "failed"):
        d = root / stage
        if not d.exists():
            continue
        for job in d.iterdir():
            if not job.is_dir():
                continue
            intake = job / "intake.yaml"
            contact = job / "CONTACT_EMAIL.txt"
            if intake.exists() and not contact.exists():
                try:
                    data = yaml.safe_load(intake.read_text(encoding="utf-8", errors="replace"))
                    email = _get_email(data)
                    if email:
                        contact.write_text(email + "\n", encoding="utf-8")
                        repaired += 1
                    else:
                        missing.append(str(job))
                except Exception:
                    missing.append(str(job))

    print(f"Repaired CONTACT_EMAIL.txt: {repaired}")
    if missing:
        print("Still missing email in intake.yaml (first 10):")
        for p in missing[:10]:
            print(" -", p)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
