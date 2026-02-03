<!-- markdownlint-disable MD007 MD022 MD029 MD032 MD034 -->

# EVIDEX — Setup checklist (end-to-end)

This repo has two halves:

- **Local (Windows):** generates packs and watches a synced folder.
- **Google (Forms/Drive + Apps Script):** creates job folders and sends emails.

## A) Local setup (Windows)

1. Create venv + install deps

  - Run: `powershell -ExecutionPolicy Bypass -File .\run_ui.ps1`
    - This creates `.venv` (if missing), installs requirements, then launches the UI.

2. Choose a watch root

  - Default is `_watch_root` in this repo.
  - For production, use the local path of your Google Drive “EvidenceEngine” folder, e.g. `C:\EvidenceEngine`.
  - Optional: set `EVIDEX_WATCH_ROOT` in `evidex.env`.

3. Configure payment link(s)

  - Set `PAYPAL_LINK` (and/or `PAYMENT_LINK`) in `evidex.env`.
  - The desktop UI can edit and save these.

4. Start the watcher

  - In the desktop UI, click **Start watcher**.

5. Optional: enable local AI (Ollama)

  - Install Ollama: https://ollama.com/download
  - In the desktop UI → **Watcher** tab:
    - Set `OLLAMA_BASE_URL` (default `http://localhost:11434/v1`)
    - Set `OLLAMA_MODEL` (e.g. `llama3.2:latest`)
    - Click **Start Ollama** (optional), **Pull model**, then **Test completion**
    - Turn on **Enable AI** and (optionally) **AI for narrative**

## B) Google setup (Forms → Drive job folders)

### 1) Create the Form

- Use `scripts/google_forms/create_grant_reporting_form.gs` in Apps Script, or manually create a Form.
- The generator script creates a “universal” intake with a first question: **In which capacity or field are you?**
  - The form then branches into a relevant section (grant, corporate compliance, university/research, consultancy, other).
- Ensure the Form collects the submitter’s email (recommended).
- Recommended question: a **File upload** field titled exactly:
  - `Upload files (PDF/DOCX/XLSX)`

### 2) Create Drive root

- In Google Drive, create folder `EvidenceEngine`.
- Inside it create: `incoming`, `processing`, `done`, `failed`, `deliveries`.

### 3) Install the Form → Drive script

- Paste `scripts/google_forms/form_submit_to_drive_jobs.gs` into Apps Script.
- Set:
  - `FORM_ID`
  - `EVIDENCE_ENGINE_ROOT_FOLDER_ID`
- Run `setupTrigger()` once to create an installable **onFormSubmit** trigger.

This automation:

- Creates the job folder under `incoming/`
- Writes `intake.yaml`
- Moves uploaded files into `uploads/`
- Emails the submitter with upload/payment instructions

### 4) Install the delivery emailer (optional)

- Paste `scripts/google_forms/drive_delivery_emailer.gs` into Apps Script.
- In Script Properties set:
  - `EVIDENCE_ENGINE_ROOT_FOLDER_ID`
  - Optional: `REQUIRE_PAYMENT=1`
- Run `setupDeliveryTrigger()` once.

This automation:

- Scans `done/` periodically
- Emails the client when `DELIVERABLE.zip` exists
- Writes `SENT.txt` to prevent duplicate sends

### 5) PayPal automation (optional, recommended if you require payment)

If you want **hands-off PayPal payment marking**, deploy the Apps Script Web App webhook receiver.

1. Paste `scripts/google_forms/paypal_web_app.gs` into the same Apps Script project.
2. In Script Properties set:
  - `PAYPAL_ENV` = `sandbox` or `live`
  - `PAYPAL_CLIENT_ID`
  - `PAYPAL_CLIENT_SECRET`
  - `PAYPAL_WEBHOOK_ID`
  - `EVIDENCE_ENGINE_ROOT_FOLDER_ID` (so the webhook can write diagnostic logs)
3. Deploy → **New deployment** → **Web app**:
  - Execute as: **Me**
  - Who has access: **Anyone** (PayPal must reach it)
4. In PayPal Developer dashboard:
  - Create a Webhook pointing to the Web App URL
  - Subscribe to `PAYMENT.CAPTURE.COMPLETED`

When a payment completes, the webhook writes `PAID.txt` and `PAYMENT_RECEIPT.txt` into the job folder using the PayPal order’s `custom_id` (set to the Drive job folder ID).

### 6) PayFast automation (recommended for South Africa)
PayFast is often the lowest-friction option for South African clients.

This repo’s web app endpoint in [scripts/google_forms/paypal_web_app.gs](scripts/google_forms/paypal_web_app.gs) also accepts **PayFast ITN** posts and will write `PAID.txt` automatically.

1. In Apps Script, deploy the Web App (same as the PayPal section).
2. In Script Properties set:
  - `PAYFAST_ENV` = `sandbox` or `live`
  - `PAYFAST_MERCHANT_ID`
  - `PAYFAST_MERCHANT_KEY`
  - Optional: `PAYFAST_PASSPHRASE`
  - Recommended: `PAYFAST_NOTIFY_URL` = your Apps Script Web App URL
  - Optional: `PAYFAST_RETURN_URL`, `PAYFAST_CANCEL_URL`
  - Optional: `PAYFAST_CURRENCY` (default `ZAR`), `PAYFAST_AMOUNT` (or leave blank to derive from `*_AMOUNT_CENTS`)
3. In the intake script, ensure PayFast links are enabled (default in this repo).

When PayFast sends an ITN with `payment_status=COMPLETE`, the web app validates it (signature + PayFast validate endpoint) and then creates `PAID.txt` / `PAYMENT_RECEIPT.txt` in the correct job folder.

## C) Optional: inbox monitoring (Gmail)

EVIDEX can show an unread count for a Gmail inbox if you configure IMAP.

1) In Gmail settings:

- Enable IMAP
- Create an App Password (recommended)

2) Set these in `evidex.env`:

- `EVIDEX_EMAIL_IMAP_HOST=imap.gmail.com`
- `EVIDEX_EMAIL_IMAP_PORT=993`
- `EVIDEX_EMAIL_IMAP_USER=evidex.ops@gmail.com`
- `EVIDEX_EMAIL_IMAP_PASSWORD=...` (App Password)

If these are not set, the UI will still provide a one-click “Open Gmail inbox” shortcut.
