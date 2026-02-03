# How the system emails people (end-to-end)

There are **two separate email moments**, both handled by **Google Apps Script** (not by the Python engine).

## 1) Intake confirmation email (immediately on Form submit)

**Where it happens:** [scripts/google_forms/form_submit_to_drive_jobs.gs](scripts/google_forms/form_submit_to_drive_jobs.gs)

**What triggers it:** Google Form submission → `onFormSubmit(e)` runs.

**What it emails:**

- A link to the **uploads folder** created in Drive for that job.
- Optional payment link(s):
  - Stripe Checkout URL if `ENABLE_STRIPE_CHECKOUT = true`
  - PayPal Checkout approval URL if `ENABLE_PAYPAL_LINK = true` and Script Properties `PAYPAL_CLIENT_ID`/`PAYPAL_CLIENT_SECRET` are set
  - PayPal.me fallback link if `ENABLE_PAYPAL_LINK = true` and only `PAYPAL_ME_LINK` is set

**How it sends:** `MailApp.sendEmail({ to, subject, body })`

**Important notes:**

- The recipient email is taken from the Form answer `Email for delivery`.
- This email is only as reliable as your Google account’s sending limits and Workspace policies.

## 2) Delivery email (after the ZIP is generated)

**Where it happens:** [scripts/google_forms/drive_delivery_emailer.gs](scripts/google_forms/drive_delivery_emailer.gs)

**What triggers it:** a time-based Apps Script trigger (every 5 minutes) runs `scanAndSendDeliveries()`.

**What it checks:**

- Looks in Drive: `EvidenceEngine/done/<job>/`
- Requires:
  - `DELIVERABLE.zip` (created by the local watcher and synced back to Drive)
  - `CONTACT_EMAIL.txt`
- Optional:
  - if Script Property `REQUIRE_PAYMENT=1`, it also requires `PAID.txt`
- Prevents double sends using `SENT.txt`

**What it emails:**

- Link to `DELIVERABLE.zip`
- Link to the job folder (audit trail)

**How it sends:** `MailApp.sendEmail(...)`

## What the Python engine does (and does NOT do)

The Python engine:

- generates the ZIP (`DELIVERABLE.zip`) and the pack folder
- writes invoice + suggested email text into the pack as artifacts:
  - `INVOICE.docx`
  - `DELIVERY_EMAIL.txt`

It does **not** send email directly.

## Payment gating (how processing is blocked until paid)

- If your watcher machine has `REQUIRE_PAYMENT=1`, the job will only process when `PAID.txt` exists in the job folder.
- Stripe automation can create `PAID.txt` via webhook.
- PayPal automation can create `PAID.txt` via webhook if you deploy the Apps Script Web App in [scripts/google_forms/paypal_web_app.gs](scripts/google_forms/paypal_web_app.gs).
- Manual fallback: you confirm payment and then create `PAID.txt`.

## Quick checklist if emails aren’t sending

- Apps Script trigger installed?
  - `setupTrigger()` for intake emails
  - `setupDeliveryTrigger()` for delivery emails
- Script has permissions approved for Mail/Drive?
- Workspace policy blocking external emails?
- Daily sending quota reached?
