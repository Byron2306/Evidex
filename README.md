# Evidence Pack Engine (Grant Reporting)

This repo generates a ready-to-submit **AI Evidence & Compliance Pack — 48-Hour Delivery** as a ZIP.

## Sector implemented (fast onboarding + high margin)
**NGO / grant-funded programs (donor reporting + audits).**

This same evidence-pack workflow also fits:
- Corporate audit readiness (annual audit support)
- Tax/VAT evidence packs (time-bound, urgency-driven)
- ESG/CSI reporting support (quarterly/annual)
- Universities/research offices (grant closeout + sponsor compliance)

Why this sector:
- Inputs already exist (logframe/KPI sheet, donor templates, receipts, emails, M&E notes)
- Recurring deadlines + high reporting pressure
- Consultants can resell packs in batches (tiered pricing supported)

## Pricing (configurable)
The engine can generate invoices using either:
- defaults from environment variables, or
- per-job overrides in `intake.yaml` under `billing:`.

Recommended competitive defaults (USD):
- NGO/grant-funded: `NGO_UNIT_PRICE=500`
- Corporate: `CORPORATE_UNIT_PRICE=900` (keeps you under $1k)
- Consultancy/reseller: `CONSULTANCY_UNIT_PRICE=400` with `billing.quantity` set to 5/10 for batch orders

Seasonal urgency angles (use in outreach):
- **Audit season:** evidence table + sources folder aligned to audit sampling
- **Tax/VAT deadlines:** reconcile claims to source documents quickly
- **Year-end/board packs:** executive summary + traceable appendices
- **Grant closeouts (universities/NGOs):** sponsor-ready documentation bundle

Optional service naming:
- `SERVICE_NAME` (global)
- `NGO_SERVICE_NAME`, `CORPORATE_SERVICE_NAME`, `CONSULTANCY_SERVICE_NAME`

## What the tool outputs (exact)
A ZIP containing:
- Executive summary (2 pages) (`00_EXEC_SUMMARY/`)
- Evidence table (KPIs → proof → sources) (`01_EVIDENCE_TABLE/`)
- Narrative justification (`02_NARRATIVE/`)
- Clean folder structure with sources copied (`03_SOURCES/`)
- Optional appendix (email/doc summaries) (`04_APPENDIX/` when enabled)

## Quick start
1) Create a virtual env and install deps:

```bash
python -m venv .venv
.venv\\Scripts\\pip install -r requirements.txt
```

2) Run the sample:

```bash
.venv\\Scripts\\python -m evidence_pack_engine.cli \
  generate \
  --intake samples/grant_reporting/intake.yaml \
  --uploads samples/grant_reporting/uploads \
  --out output
```

It will create a pack folder and a `*.zip` under `output/`.

## Fully automated (hands-free)

This is the "quiet engine" mode: you run one watcher, then just drop client jobs into a synced folder.

### Folder layout
Pick a root folder (ideally inside OneDrive/Drive sync), e.g. `C:\EvidenceEngine`:

```
C:\EvidenceEngine\
  incoming\
    <job_name>\
      intake.yaml
      uploads\
        ...client files...
  processing\
  done\
  failed\
  deliveries\
```

### Run the watcher

```bash
.venv\\Scripts\\python -m evidence_pack_engine.cli watch --root C:\\EvidenceEngine
```

When a job folder becomes ready (has `intake.yaml` + `uploads/`), the engine will:
- Generate the evidence pack folder + ZIP into `deliveries/`
- Write `INVOICE.docx` and `DELIVERY_EMAIL.txt` into the pack folder
- Copy `DELIVERABLE.zip` into the job folder (for easy Drive-based sharing)
- Move the job folder into `done/` (or `failed/` with `ERROR.txt`)

### Optional: require payment before processing
If you want **fully automated payment gating**, set an environment variable:

- `REQUIRE_PAYMENT=1`

When enabled, a job is only considered ready once the job folder contains:

- `PAID.txt` (created automatically by your payment automation)

This prevents the watcher from processing unpaid submissions.

In a Stripe-based setup, the webhook writes `PAID.txt` into the Drive job folder, and Drive sync brings it to your PC.

### Optional: fully automated emails (payment + delivery)
- Payment link email (on submit): enable Stripe Checkout link generation in `scripts/google_forms/form_submit_to_drive_jobs.gs`.
- Delivery email (when ready): add `scripts/google_forms/drive_delivery_emailer.gs` and run `setupDeliveryTrigger()`.

### Billing variables (optional)
Set these environment variables to customize invoice/payment text:
- `VENDOR_NAME`
- `PAYMENT_LINK`
- `PAYMENT_INSTRUCTIONS`
- `PAYMENT_SHORT`
- `PAYPAL_LINK` (optional)
- `PAYPAL_SHORT` (optional)
- `INVOICE_CURRENCY` (default `USD`)

You can also persist settings in a simple env file:
- Create `evidex.env` in the repo root (or set `EVIDEX_ENV_PATH` to point elsewhere)
- The CLI and desktop UI will load it automatically

## Sync the root with Google Form (simplest)

The simplest reliable path is:
**Google Form submission → Google Drive job folder → Drive for Desktop sync → local watcher runs.**

### Step 1 — Create the Drive root
1) In Google Drive, create a folder called `EvidenceEngine`.
2) Inside it, create: `incoming/`, `processing/`, `done/`, `failed/`, `deliveries/`.

### Step 2 — Sync it to Windows
Install **Google Drive for Desktop** and ensure the `EvidenceEngine` folder is available locally.

Two options:
- **Mirror** (best): creates a real local folder (best for file watchers).
- **Stream**: appears as a drive letter; also works, but events can be less consistent.

Set your watcher root to the synced local path, e.g. `C:\\EvidenceEngine`.

### Step 3 — Create jobs from Form submissions (Apps Script)
Use the script in `scripts/google_forms/form_submit_to_drive_jobs.gs`.

It creates, per submission:
`EvidenceEngine/incoming/<job_name>/intake.yaml` and `uploads/`.

When the client uploads files into `uploads/`, your local watcher will generate the pack automatically.

## Using an LLM (optional)
If an LLM is enabled, the engine can produce better evidence summaries and (optionally) a narrative.

Environment variables:
- `OPENAI_API_KEY` (required for hosted OpenAI; not required for localhost Ollama)
- `OPENAI_MODEL` (default: `gpt-4o-mini`)
- `OPENAI_BASE_URL` (optional; for OpenAI-compatible gateways)

Local Ollama (free) option:
- Run Ollama locally, then set:
  - `OLLAMA_BASE_URL=http://localhost:11434/v1`
  - `OLLAMA_MODEL=llama3.2` (or `qwen2.5`, `phi3`, `gemma3`)
  - (no key required for localhost; the engine uses a safe dummy key)

Model map (optional): use different local models for different tasks:
- Evidence summaries: `OLLAMA_MODEL_SUMMARY=qwen2.5`
- Narrative writing: `OLLAMA_MODEL_NARRATIVE=llama3.2`

Aliases also supported:
- `LLM_MODEL_SUMMARY`, `LLM_MODEL_NARRATIVE`
- `OPENAI_MODEL_SUMMARY`, `OPENAI_MODEL_NARRATIVE`

Feature toggles:
- Disable all LLM usage (global kill-switch): `LLM_DISABLED=1`
- Disable/enable KPI evidence summaries via LLM (default enabled): `SUMMARY_USE_LLM=0|1`
- Disable/enable narrative generation via LLM (default disabled): `NARRATIVE_USE_LLM=0|1`

## Desktop UI (Windows)
Launch a simple dashboard to start/stop the watcher and monitor jobs:

Recommended (no venv activation needed):
- `powershell -ExecutionPolicy Bypass -File .\run_ui.ps1`

Or run directly:
- `C:/Users/User/Desktop/evidence-pack-engine/.venv/Scripts/python.exe -m evidence_pack_engine.desktop`

The UI expects a watch root with `incoming/`, `processing/`, `done/`, `failed/`, `deliveries/`.
Default: `_watch_root` in the repo. Override via `EVIDEX_WATCH_ROOT`.

### Logo in the UI
Place a PNG logo here:
- `assets/evidex_logo.png`

Or set an explicit path:
- `EVIDEX_LOGO_PATH=C:\\path\\to\\logo.png`

### Dashboard (counts + charts)
The Desktop UI includes a Dashboard tab showing:
- incoming/processing/done/failed counts
- ready-to-email vs sent counts (based on `SENT.txt`)
- simple charts for requests/completions and paid revenue

Optional profit estimation:
- Set `UNIT_COST` (per pack) and the dashboard will show an estimated profit line.

### Optional: inbox monitoring (Gmail)
The Desktop UI can show a Gmail **unread count** if you enable IMAP and provide an App Password.
Set these in `evidex.env`:
- `EVIDEX_EMAIL_IMAP_USER=evidex.ops@gmail.com`
- `EVIDEX_EMAIL_IMAP_PASSWORD=...`

If you don’t set them, the UI still provides an “Open Gmail inbox” shortcut.

Optional: generate narrative text via LLM (otherwise template-based):
- `NARRATIVE_USE_LLM=1`

Optional: disable all AI from the UI/env:
- `LLM_DISABLED=1`

Without an API key, the generator still runs and produces a complete, structured pack using deterministic heuristics.

## Email automation (how clients get emails)
See [docs/EMAIL_FLOW.md](docs/EMAIL_FLOW.md) for the exact flow (intake email + delivery email) and what sends what.

## Intake form
Use the questions in `intake/grant_reporting_form_questions.md` to build your Google Form.

If you want it created automatically in your Google account, use the Apps Script in `scripts/google_forms/`.

## Setup checklist (end-to-end)
See [docs/SETUP_CHECKLIST.md](docs/SETUP_CHECKLIST.md) for the full end-to-end initialization steps:
- local watcher/UI
- Form → Drive automation
- delivery email trigger
- optional inbox monitoring

Local helper script (creates `_watch_root` folders and a starter `evidex.env`):
- `powershell -ExecutionPolicy Bypass -File .\scripts\setup_local.ps1`
