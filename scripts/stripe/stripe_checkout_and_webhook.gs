/**
 * Stripe Checkout + Webhook (Google Apps Script)
 *
 * Goal: fully automated payment gating for EvidenceEngine jobs.
 *
 * Flow:
 * 1) Your Form submit script creates a Drive job folder and knows its folder ID.
 * 2) It calls createCheckoutSessionUrl_(jobName, jobFolderId, customerEmail) to get a Stripe Checkout URL.
 * 3) Customer pays.
 * 4) Stripe webhook calls doPost(e). When payment completes, this script writes PAID.txt into that Drive job folder.
 * 5) Your local watcher (REQUIRE_PAYMENT=1) sees PAID.txt via Drive sync and processes the job.
 *
 * IMPORTANT: Put secrets in Script Properties, NOT in code.
 * - STRIPE_SECRET_KEY: your Stripe secret key (sk_...)
 * - STRIPE_WEBHOOK_SECRET: webhook signing secret (whsec_...)
 * - STRIPE_CURRENCY: e.g. USD
 * - STRIPE_AMOUNT_CENTS: e.g. 50000
 * - STRIPE_SUCCESS_URL: https://...  (can be a generic thank-you page)
 * - STRIPE_CANCEL_URL: https://...
 */

function _getProp_(name) {
  var v = PropertiesService.getScriptProperties().getProperty(name);
  return String(v || '').trim();
}

function _requireProp_(name) {
  var v = _getProp_(name);
  if (!v) throw new Error('Missing Script Property: ' + name);
  return v;
}

function _toHex_(bytes) {
  var out = [];
  for (var i = 0; i < bytes.length; i++) {
    var b = (bytes[i] + 256) % 256;
    out.push((b < 16 ? '0' : '') + b.toString(16));
  }
  return out.join('');
}

function _verifyStripeSignature_(rawBody, signatureHeader, webhookSecret) {
  // Stripe signs: "t=...,v1=..." and payload is `${t}.${rawBody}`.
  var sig = String(signatureHeader || '');
  var tMatch = sig.match(/(?:^|,)\s*t=(\d+)/);
  var v1Match = sig.match(/(?:^|,)\s*v1=([0-9a-f]+)/i);
  if (!tMatch || !v1Match) return false;

  var t = tMatch[1];
  var v1 = v1Match[1];
  var signedPayload = t + '.' + rawBody;

  var macBytes = Utilities.computeHmacSha256Signature(signedPayload, webhookSecret);
  var macHex = _toHex_(macBytes);
  return macHex.toLowerCase() === String(v1).toLowerCase();
}

function createCheckoutSessionUrl_(jobName, jobFolderId, customerEmail) {
  var secretKey = _requireProp_('STRIPE_SECRET_KEY');

  var currency = _getProp_('STRIPE_CURRENCY') || 'USD';
  var amountCents = _getProp_('STRIPE_AMOUNT_CENTS') || '50000';
  var successUrl = _requireProp_('STRIPE_SUCCESS_URL');
  var cancelUrl = _requireProp_('STRIPE_CANCEL_URL');

  // Stripe expects application/x-www-form-urlencoded
  var payload = {
    'mode': 'payment',
    'success_url': successUrl,
    'cancel_url': cancelUrl,
    'client_reference_id': jobName,
    'metadata[job_folder_id]': jobFolderId,
    'metadata[job_name]': jobName,
    'line_items[0][quantity]': '1',
    'line_items[0][price_data][currency]': currency,
    'line_items[0][price_data][unit_amount]': String(amountCents),
    'line_items[0][price_data][product_data][name]': 'AI Evidence & Compliance Pack — 48-Hour Delivery'
  };

  if (customerEmail) {
    payload['customer_email'] = customerEmail;
  }

  var resp = UrlFetchApp.fetch('https://api.stripe.com/v1/checkout/sessions', {
    method: 'post',
    payload: payload,
    headers: {
      Authorization: 'Bearer ' + secretKey
    },
    muteHttpExceptions: true
  });

  var code = resp.getResponseCode();
  var text = resp.getContentText();
  if (code < 200 || code >= 300) {
    throw new Error('Stripe session create failed (' + code + '): ' + text);
  }

  var data = JSON.parse(text);
  if (!data || !data.url) throw new Error('Stripe response missing checkout URL');
  return data.url;
}

/**
 * Deploy as a Web App:
 * - Execute as: Me
 * - Who has access: Anyone
 *
 * Then configure Stripe Webhook endpoint to that URL.
 */
function doPost(e) {
  var webhookSecret = _requireProp_('STRIPE_WEBHOOK_SECRET');
  var sigHeader = (e && e.postData && e.postData.headers) ? e.postData.headers['Stripe-Signature'] : null;
  var rawBody = (e && e.postData) ? e.postData.contents : '';

  if (!_verifyStripeSignature_(rawBody, sigHeader, webhookSecret)) {
    return ContentService.createTextOutput('Invalid signature').setMimeType(ContentService.MimeType.TEXT);
  }

  var evt = JSON.parse(rawBody);
  var type = evt && evt.type;

  // We key off checkout session completion.
  if (type === 'checkout.session.completed' || type === 'checkout.session.async_payment_succeeded') {
    var obj = evt.data && evt.data.object;
    var meta = (obj && obj.metadata) ? obj.metadata : {};
    var jobFolderId = String(meta.job_folder_id || '').trim();
    var jobName = String(meta.job_name || '').trim();

    if (jobFolderId) {
      var folder = DriveApp.getFolderById(jobFolderId);
      folder.createFile(
        'PAID.txt',
        'Paid via Stripe at ' + new Date().toISOString() + '\n' +
          'job_name=' + jobName + '\n' +
          'stripe_session=' + String(obj.id || '') + '\n',
        MimeType.PLAIN_TEXT
      );
    }
  }

  return ContentService.createTextOutput('ok').setMimeType(ContentService.MimeType.TEXT);
}
