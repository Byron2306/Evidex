<!-- markdownlint-disable MD007 MD022 MD029 MD031 MD032 -->

# Google Form → Drive job folders → Local watcher (Full automation)

This is the cleanest end-to-end automation without servers.

## What you get

- Each Google Form submission creates a Drive job folder:
  - `EvidenceEngine/incoming/<job_name>/intake.yaml`
  - `EvidenceEngine/incoming/<job_name>/uploads/`
- Google Drive for Desktop syncs `EvidenceEngine/` to your Windows machine.
- The watcher processes jobs when real upload files appear in `uploads/`.

## 1) Create Drive root
1. Google Drive → create folder `EvidenceEngine`
2. Inside create: `incoming`, `processing`, `done`, `failed`, `deliveries`
3. Copy the folder ID:
   - Open the folder in browser
   - URL looks like: `https://drive.google.com/drive/folders/<FOLDER_ID>`

## 2) Sync to Windows

Install **Google Drive for Desktop**.

Recommended:

- Use **Mirror files**
- Choose a local location like `C:\EvidenceEngine` (or your user profile)

## 3) Add Apps Script trigger on Form submit
1. Open your Google Form → **Extensions → Apps Script**
2. Paste `form_submit_to_drive_jobs.gs` into `Code.gs`
3. Set these variables at the top:
   - `FORM_ID` (Form settings / URL)
   - `EVIDENCE_ENGINE_ROOT_FOLDER_ID` (Drive folder ID from step 1)
4. Run `setupTrigger()` once → approve permissions

## 3b) Optional: auto-send Stripe payment link

In the same script (`form_submit_to_drive_jobs.gs`):

- Set `ENABLE_STRIPE_CHECKOUT = true`
- In Apps Script → Project settings → Script properties, set:
   - `STRIPE_SECRET_KEY`
   - `STRIPE_WEBHOOK_SECRET` (if you also deploy the webhook)
   - `STRIPE_CURRENCY` (e.g. USD)
   - `STRIPE_AMOUNT_CENTS` (e.g. 50000)
   - `STRIPE_SUCCESS_URL`
   - `STRIPE_CANCEL_URL`

Tiered pricing (optional, recommended):

- `NGO_AMOUNT_CENTS` (e.g. 50000)
- `CORPORATE_AMOUNT_CENTS` (e.g. 90000)
- `CONSULTANCY_AMOUNT_CENTS` (e.g. 40000)

Optional product naming:

- `STRIPE_PRODUCT_NAME` (fallback)
- `NGO_PRODUCT_NAME`, `CORPORATE_PRODUCT_NAME`, `CONSULTANCY_PRODUCT_NAME`

If your Form includes these optional questions, the script will populate `billing:` in `intake.yaml` and will also set the Stripe quantity accordingly:

- `Client type`
- `Order size (packs)`
- `Currency`

If `Client type` is left blank, the script will infer it from `Pack purpose` and `Role` (e.g., tax/audit/esg/university → Corporate, role=Consultant → Consultancy, donor/grant → NGO/grant-funded).

This will email the customer a Checkout link as soon as they submit.

To enforce payment before processing, set `REQUIRE_PAYMENT=1` on your watcher machine.

## 3b-alt) Optional: PayPal link (manual, simplest)

If you want the lowest-complexity/lowest-cost setup, you can include a PayPal payment link in the intake email.

- In [scripts/google_forms/form_submit_to_drive_jobs.gs](scripts/google_forms/form_submit_to_drive_jobs.gs) set: `ENABLE_PAYPAL_LINK = true`
- In Apps Script → Project settings → Script properties, set:
   - `PAYPAL_ME_LINK` (e.g. `https://paypal.me/<yourname>`)

This creates `PAYPAL_URL.txt` in the job folder and includes the link in the intake email.
Payment gating remains the same: create `PAID.txt` in the job folder once payment is confirmed.

## 3b-alt2) Optional: PayPal Checkout + webhook (automatic)

If you want PayPal payments to automatically mark jobs as paid (writes `PAID.txt`), use PayPal Checkout + an Apps Script Web App webhook receiver.

1) In Apps Script → Project settings → Script properties, set:
   - `PAYPAL_ENV` = `sandbox` or `live`
   - `PAYPAL_CLIENT_ID`
   - `PAYPAL_CLIENT_SECRET`
   - `PAYPAL_WEBHOOK_ID`
   - Optional: `PAYPAL_RETURN_URL`, `PAYPAL_CANCEL_URL`
2) In `form_submit_to_drive_jobs.gs` set: `ENABLE_PAYPAL_LINK = true`
   - The script will create a PayPal Checkout order and email an approval URL.
   - It stores `PAYPAL_APPROVAL_URL.txt` and `PAYPAL_ORDER_ID.txt` in the job folder.
3) Add `paypal_web_app.gs` to the same Apps Script project.
4) Deploy the Apps Script as a Web App:
   - Execute as: **Me**
   - Who has access: **Anyone**
5) In PayPal Developer dashboard, create a webhook pointing to the Web App URL and subscribe to:
   - `PAYMENT.CAPTURE.COMPLETED`

When PayPal posts a verified webhook, the web app fetches the order and uses `purchase_units[0].custom_id` (set to the Drive job folder ID) to locate the correct job and create `PAID.txt`.

## 3c) Optional: auto-email deliverables when ready

Use `drive_delivery_emailer.gs` in this repo.

1) In Apps Script, create a new file and paste `drive_delivery_emailer.gs`.
2) In Script properties, set:
    - `EVIDENCE_ENGINE_ROOT_FOLDER_ID` (same root folder you already created)
    - Optional: `REQUIRE_PAYMENT` = `1`
3) Run `setupDeliveryTrigger()` once.

It will scan `EvidenceEngine/done/` every 5 minutes and email clients when `DELIVERABLE.zip` appears.

## 4) Run the watcher

```powershell
C:/Users/User/Desktop/evidence-pack-engine/.venv/Scripts/python.exe -m evidence_pack_engine.cli watch --root C:\EvidenceEngine
```

## Notes

- Apps Script can email the client the uploads folder link. This depends on your Workspace sharing rules.
- The watcher only processes jobs once at least one real file exists in `uploads/`.
