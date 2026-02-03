from __future__ import annotations

import argparse
from pathlib import Path

from .envfile import load_default_env
from .pack import write_pack
from .watcher import WatchConfig, watch


def _cmd_generate(args: argparse.Namespace) -> int:
    intake = Path(args.intake).resolve()
    uploads = Path(args.uploads).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    zip_path, _pack_root, _flags = write_pack(
        intake_path=intake,
        uploads_dir=uploads,
        out_dir=out,
        job_name=intake.stem,
    )

    print(str(zip_path))
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    cfg = WatchConfig(
        incoming_dir=root / "incoming",
        processing_dir=root / "processing",
        done_dir=root / "done",
        failed_dir=root / "failed",
        deliveries_dir=root / "deliveries",
    )
    print(f"Watching: {cfg.incoming_dir}")
    watch(cfg)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="evidence-pack-engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="Generate an evidence pack zip")
    g.add_argument("--intake", required=True, help="Path to intake YAML/JSON")
    g.add_argument("--uploads", required=True, help="Folder of uploaded source files")
    g.add_argument("--out", required=True, help="Output folder")
    g.set_defaults(func=_cmd_generate)

    w = sub.add_parser("watch", help="Watch a folder for incoming jobs and auto-generate packs")
    w.add_argument("--root", required=True, help="Root folder containing incoming/processing/done/failed/deliveries")
    w.set_defaults(func=_cmd_watch)

    return p


def main() -> int:
    # Load optional runtime settings (payment links, toggles, model config, etc.)
    # without requiring users to manually set environment variables.
    load_default_env(override=False)
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
