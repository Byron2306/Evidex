# Stripe payment automation (no server)

This enables **fully automated payment gating**:

- Form submission creates a job folder in Drive.
- Customer pays via Stripe Checkout.
- Stripe webhook writes `PAID.txt` into the Drive job folder.
- Your local watcher runs with `REQUIRE_PAYMENT=1` and only processes paid jobs.

## 1) Add the script
1. Open https://script.google.com → **New project**
2. Create a file `stripe_checkout_and_webhook.gs` and paste in this repo’s version.
3. Deploy → **New deployment** → **Web app**
   - Execute as: **Me**
   - Who has access: **Anyone**
4. Copy the Web App URL.

## 2) Configure Script Properties
In Apps Script: Project settings → Script properties:

- `STRIPE_SECRET_KEY` = `sk_...`
- `STRIPE_WEBHOOK_SECRET` = `whsec_...`
- `STRIPE_CURRENCY` = `USD`
- `STRIPE_AMOUNT_CENTS` = `50000`
- `STRIPE_SUCCESS_URL` = a generic thank-you page URL
- `STRIPE_CANCEL_URL` = a generic cancel page URL

## 3) Configure Stripe webhook
In Stripe Dashboard:
- Developers → Webhooks → Add endpoint
- Endpoint URL = your Apps Script Web App URL
- Events to send:
  - `checkout.session.completed`
  - `checkout.session.async_payment_succeeded`

Copy the signing secret into `STRIPE_WEBHOOK_SECRET`.

## 4) Enable payment gating locally
Set env var on your watcher machine:

- `REQUIRE_PAYMENT=1`

Now jobs won’t run until `PAID.txt` exists in the job folder.

## 5) Connect to your Form submit script
Your Form submit script needs to:
- create the job folder
- call `createCheckoutSessionUrl_(jobName, jobFolderId, customerEmail)`
- email the returned URL to the customer

Simplest: paste the `createCheckoutSessionUrl_` function into the same Apps Script project as your form submit handler.
