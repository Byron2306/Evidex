# EVIDEX Google Automation

This folder contains the Google Apps Script files that power:

- Creating the EVIDEX intake Google Form
- Creating Drive job folders on form submit
- (Optional) emailing deliverables once `DELIVERABLE.zip` syncs back into Drive

## Recommended setup path

Use the EVIDEX desktop app (Watcher tab) with Google Apps Script buttons:

1. **Init clasp project…**
2. **Create intake form** (optional)
3. **Configure IDs** (sets Script Properties)
4. Run **setupTrigger()** and **setupDeliveryTrigger()**

## Script properties used

- `FORM_ID`
- `EVIDENCE_ENGINE_ROOT_FOLDER_ID`

(Optionally) `EVIDEX_LOGO_FILE_ID`

## Payment automation (optional)

### Require payment before delivery

- Set Script Property `REQUIRE_PAYMENT=1`.
- The delivery emailer waits for `PAID.txt` in `done/<job>/`.

### PayPal Checkout + webhook auto-marking

If you want PayPal payments to automatically create `PAID.txt`, deploy the web app in `paypal_web_app.gs`.

Script Properties:

- `PAYPAL_ENV` = `sandbox` or `live`
- `PAYPAL_CLIENT_ID`
- `PAYPAL_CLIENT_SECRET`
- `PAYPAL_WEBHOOK_ID`
- Optional: `PAYPAL_RETURN_URL`, `PAYPAL_CANCEL_URL`

Deployment:

- Apps Script → Deploy → New deployment → Web app
- Execute as: Me
- Who has access: Anyone

PayPal webhook event:

- Subscribe to `PAYMENT.CAPTURE.COMPLETED`

### PayFast ITN auto-marking (recommended for South Africa)

The same web app endpoint in `paypal_web_app.gs` also accepts PayFast ITN posts and will create `PAID.txt` automatically.

Script Properties:

- `PAYFAST_ENV` = `sandbox` or `live`
- `PAYFAST_MERCHANT_ID`
- `PAYFAST_MERCHANT_KEY`
- Optional: `PAYFAST_PASSPHRASE`
- Recommended: `PAYFAST_NOTIFY_URL` = your Apps Script Web App URL
- Optional: `PAYFAST_RETURN_URL`, `PAYFAST_CANCEL_URL`, `PAYFAST_CURRENCY`, `PAYFAST_AMOUNT`
