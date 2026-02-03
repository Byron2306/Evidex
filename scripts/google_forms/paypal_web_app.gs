/**
 * PayPal Webhook Receiver (Google Apps Script Web App)
 *
 * Purpose:
 * - Provide a public HTTPS endpoint for PayPal webhooks.
 * - Verify webhook signatures with PayPal.
 * - On successful payment, mark the corresponding Drive job folder as paid by writing PAID.txt.
 *
 * Script Properties required:
 * - PAYPAL_CLIENT_ID
 * - PAYPAL_CLIENT_SECRET
 * - PAYPAL_WEBHOOK_ID
 * Optional:
 * - PAYPAL_ENV: 'live' (default) or 'sandbox'
 */

function doGet(e) {
  return ContentService
    .createTextOutput('EVIDEX PayPal webhook endpoint is running. Use POST for webhooks.')
    .setMimeType(ContentService.MimeType.TEXT);
}

function doPost(e) {
  // Always respond 200 quickly to avoid webhook retries storms. If verification fails we still
  // return 200 but record diagnostics for operators.
  try {
    var rawBody = (e && e.postData && e.postData.contents) ? String(e.postData.contents) : '';
    var contentType = '';
    try { contentType = String(e && e.postData && e.postData.type ? e.postData.type : '').toLowerCase(); } catch (_ct) { contentType = ''; }

    // Route by payload type.
    // - PayPal webhooks: JSON with event_type.
    // - PayFast ITN: x-www-form-urlencoded fields like payment_status, pf_payment_id, signature.
    var looksJson = (contentType.indexOf('application/json') >= 0) || (rawBody && rawBody.trim().charAt(0) === '{');
    if (looksJson) {
      var event = rawBody ? JSON.parse(rawBody) : {};

      var verified = _ppVerifyWebhook_(e, rawBody);
      if (!verified.ok) {
        _ppLog_('verify_failed', JSON.stringify({ reason: verified.reason || 'unknown', event: event }, null, 2));
        return ContentService.createTextOutput('ok').setMimeType(ContentService.MimeType.TEXT);
      }

      if (verified.mode && verified.mode !== 'signature') {
        _ppLog_('verify_skipped', JSON.stringify({ mode: verified.mode, note: verified.note || '', event_type: event.event_type || '' }, null, 2));
      }

      _ppHandleEvent_(event);
    } else {
      _pfHandleItn_(e, rawBody);
    }
  } catch (err) {
    _ppLog_('exception', String(err));
  }

  return ContentService.createTextOutput('ok').setMimeType(ContentService.MimeType.TEXT);
}

function _ppEnv_() {
  var env = String(PropertiesService.getScriptProperties().getProperty('PAYPAL_ENV') || 'live').trim().toLowerCase();
  if (env !== 'sandbox') env = 'live';
  return env;
}

function _ppApiBase_() {
  return _ppEnv_() === 'sandbox' ? 'https://api-m.sandbox.paypal.com' : 'https://api-m.paypal.com';
}

function _ppGetProp_(name) {
  var v = PropertiesService.getScriptProperties().getProperty(String(name));
  return String(v || '').trim();
}

function _ppRequireProp_(name) {
  var v = _ppGetProp_(name);
  if (!v) throw new Error('Missing Script Property: ' + name);
  return v;
}

function _ppLog_(name, text) {
  try {
    var rootId = String(PropertiesService.getScriptProperties().getProperty('EVIDENCE_ENGINE_ROOT_FOLDER_ID') || '').trim();
    if (!rootId) {
      Logger.log('PayPal webhook log (no root configured): ' + name + ' :: ' + text);
      return;
    }
    var root = DriveApp.getFolderById(rootId);
    var fname = '_paypal_webhook_' + String(name || 'log') + '.txt';
    var it = root.getFilesByName(fname);
    var f = it.hasNext() ? it.next() : root.createFile(fname, '', MimeType.PLAIN_TEXT);
    var prev = '';
    try {
      prev = f.getBlob().getDataAsString();
    } catch (_e) {
      prev = '';
    }
    var line = '\n[' + new Date().toISOString() + ']\n' + String(text || '') + '\n';
    f.setContent((prev || '') + line);
  } catch (e) {
    Logger.log('PayPal webhook log failed: ' + e);
  }
}

function _ppAccessToken_() {
  var cache = CacheService.getScriptCache();
  var cached = cache.get('pp_access_token');
  if (cached) return String(cached);

  var clientId = _ppRequireProp_('PAYPAL_CLIENT_ID');
  var secret = _ppRequireProp_('PAYPAL_CLIENT_SECRET');

  var basic = Utilities.base64Encode(clientId + ':' + secret);
  var resp = UrlFetchApp.fetch(_ppApiBase_() + '/v1/oauth2/token', {
    method: 'post',
    payload: { grant_type: 'client_credentials' },
    headers: {
      Authorization: 'Basic ' + basic,
      Accept: 'application/json'
    },
    muteHttpExceptions: true
  });

  var code = resp.getResponseCode();
  var body = resp.getContentText();
  if (code < 200 || code >= 300) {
    throw new Error('PayPal token failed (' + code + '): ' + body);
  }
  var data = JSON.parse(body);
  var tok = String(data && data.access_token ? data.access_token : '');
  if (!tok) throw new Error('PayPal token missing access_token');

  // Cache for ~50 minutes
  try {
    cache.put('pp_access_token', tok, 50 * 60);
  } catch (_e2) {}

  return tok;
}

function _ppVerifyWebhook_(e, rawBody) {
  try {
    var webhookId = _ppRequireProp_('PAYPAL_WEBHOOK_ID');

    // PayPal webhook verification requires these headers.
    var h = (e && e.parameter) ? e.parameter : {};

    // Apps Script exposes headers via e.postData.type? Not reliably.
    // Workaround: use e.postData.getHeaders() when present (newer runtime) else rely on parameter map.
    var headers = {};
    try {
      if (e && e.postData && typeof e.postData.getHeaders === 'function') {
        headers = e.postData.getHeaders() || {};
      }
    } catch (_e1) {
      headers = {};
    }

    function getHeader(name) {
      var n = String(name || '').toLowerCase();
      // Prefer headers object
      for (var k in headers) {
        if (String(k).toLowerCase() === n) return String(headers[k]);
      }
      // Fallback to parameter map
      for (var k2 in h) {
        if (String(k2).toLowerCase() === n) return String(h[k2]);
      }
      return '';
    }

    var transmissionId = getHeader('paypal-transmission-id');
    var transmissionTime = getHeader('paypal-transmission-time');
    var certUrl = getHeader('paypal-cert-url');
    var authAlgo = getHeader('paypal-auth-algo');
    var transmissionSig = getHeader('paypal-transmission-sig');

    if (!transmissionId || !transmissionTime || !certUrl || !authAlgo || !transmissionSig) {
      // Apps Script web apps historically do not expose inbound HTTP headers reliably.
      // If we can't read headers, we cannot use PayPal's signature verification endpoint.
      // We still proceed, but only mark jobs paid after additional safety checks.
      return { ok: true, mode: 'no_headers', note: 'PayPal headers not available in Apps Script event object; signature verification skipped' };
    }

    var tok = _ppAccessToken_();

    var verifyPayload = {
      auth_algo: authAlgo,
      cert_url: certUrl,
      transmission_id: transmissionId,
      transmission_sig: transmissionSig,
      transmission_time: transmissionTime,
      webhook_id: webhookId,
      webhook_event: rawBody ? JSON.parse(rawBody) : {}
    };

    var resp = UrlFetchApp.fetch(_ppApiBase_() + '/v1/notifications/verify-webhook-signature', {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify(verifyPayload),
      headers: {
        Authorization: 'Bearer ' + tok,
        Accept: 'application/json'
      },
      muteHttpExceptions: true
    });

    var code = resp.getResponseCode();
    var text = resp.getContentText();
    if (code < 200 || code >= 300) {
      return { ok: false, reason: 'verify call failed (' + code + '): ' + text };
    }

    var data = JSON.parse(text);
    var status = String((data && data.verification_status) ? data.verification_status : '').toUpperCase();
    if (status !== 'SUCCESS') {
      return { ok: false, reason: 'verification_status=' + status };
    }

    return { ok: true, mode: 'signature' };
  } catch (err) {
    return { ok: false, reason: String(err) };
  }
}

function _ppIsUnderRoot_(folder, rootId) {
  if (!rootId) return true;
  try {
    var targetId = String(folder.getId());
    if (targetId === String(rootId)) return true;
  } catch (_e0) {}

  var maxDepth = 12;
  var queue = [{ f: folder, d: 0 }];
  var seen = {};

  while (queue.length) {
    var item = queue.shift();
    var f = item.f;
    var d = item.d;
    if (!f || d > maxDepth) continue;

    var fid = '';
    try { fid = String(f.getId()); } catch (_e1) { fid = ''; }
    if (fid) {
      if (fid === String(rootId)) return true;
      if (seen[fid]) continue;
      seen[fid] = true;
    }

    try {
      var parents = f.getParents();
      while (parents.hasNext()) {
        var p = parents.next();
        queue.push({ f: p, d: d + 1 });
      }
    } catch (_e2) {
      // ignore
    }
  }

  return false;
}

// -------------------- PayFast ITN (South Africa) --------------------

function _pfEnv_() {
  var env = String(PropertiesService.getScriptProperties().getProperty('PAYFAST_ENV') || 'live').trim().toLowerCase();
  if (env !== 'sandbox') env = 'live';
  return env;
}

function _pfValidateUrl_() {
  return _pfEnv_() === 'sandbox'
    ? 'https://sandbox.payfast.co.za/eng/query/validate'
    : 'https://www.payfast.co.za/eng/query/validate';
}

function _pfUrlEncode_(s) {
  return encodeURIComponent(String(s || ''))
    .replace(/%20/g, '+')
    .replace(/\*/g, '%2A');
}

function _pfMd5Hex_(text) {
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, String(text || ''), Utilities.Charset.UTF_8);
  var out = '';
  for (var i = 0; i < bytes.length; i++) {
    var v = (bytes[i] < 0) ? bytes[i] + 256 : bytes[i];
    var h = v.toString(16);
    if (h.length < 2) h = '0' + h;
    out += h;
  }
  return out;
}

function _pfBuildParamString_(params, includeSignature) {
  var keys = [];
  for (var k in params) {
    if (!params.hasOwnProperty(k)) continue;
    if (!includeSignature && (k === 'signature' || k === 'pf_signature')) continue;
    keys.push(k);
  }
  keys.sort();

  var parts = [];
  for (var i = 0; i < keys.length; i++) {
    var key = keys[i];
    var val = params[key];
    if (val === null || typeof val === 'undefined') val = '';
    // e.parameter can sometimes be arrays; take first.
    if (Object.prototype.toString.call(val) === '[object Array]') {
      val = (val.length ? val[0] : '');
    }
    val = String(val);
    parts.push(String(key) + '=' + _pfUrlEncode_(val));
  }
  return parts.join('&');
}

function _pfVerifySignature_(params) {
  var received = String(params.pf_signature || params.signature || '').trim().toLowerCase();
  if (!received) return { ok: false, reason: 'missing pf_signature/signature' };

  var passphrase = String(PropertiesService.getScriptProperties().getProperty('PAYFAST_PASSPHRASE') || '').trim();
  var base = _pfBuildParamString_(params, false);
  var sigString = base;
  if (passphrase) sigString += '&passphrase=' + _pfUrlEncode_(passphrase);

  var computed = _pfMd5Hex_(sigString).toLowerCase();
  if (computed !== received) {
    return { ok: false, reason: 'signature mismatch', computed: computed, received: received };
  }
  return { ok: true };
}

function _pfValidateWithPayFast_(rawBody, params) {
  // PayFast expects the POST data echoed back to their validate endpoint.
  var payload = String(rawBody || '').trim();
  if (!payload) {
    payload = _pfBuildParamString_(params, true);
  }

  var resp = UrlFetchApp.fetch(_pfValidateUrl_(), {
    method: 'post',
    payload: payload,
    contentType: 'application/x-www-form-urlencoded',
    muteHttpExceptions: true
  });
  var code = resp.getResponseCode();
  var text = String(resp.getContentText() || '').trim();
  if (code < 200 || code >= 300) {
    return { ok: false, reason: 'validate HTTP ' + code + ': ' + text };
  }
  if (text !== 'VALID') {
    return { ok: false, reason: 'validate response: ' + text };
  }
  return { ok: true };
}

function _pfHandleItn_(e, rawBody) {
  try {
    var params = (e && e.parameter) ? e.parameter : {};
    if (!params || (!params.payment_status && !params.pf_payment_id && !params.signature && !params.pf_signature)) {
      _ppLog_('ignored', 'Non-JSON POST did not look like PayFast ITN');
      return;
    }

    // Basic merchant check
    var merchantId = String(PropertiesService.getScriptProperties().getProperty('PAYFAST_MERCHANT_ID') || '').trim();
    if (merchantId && String(params.merchant_id || '').trim() && String(params.merchant_id || '').trim() !== merchantId) {
      _ppLog_('error', 'PayFast ITN merchant mismatch. got=' + params.merchant_id + ' expected=' + merchantId);
      return;
    }

    var sigOk = _pfVerifySignature_(params);
    if (!sigOk.ok) {
      _ppLog_('verify_failed', JSON.stringify({ provider: 'payfast', reason: sigOk.reason, details: sigOk }, null, 2));
      return;
    }

    var valid = _pfValidateWithPayFast_(rawBody, params);
    if (!valid.ok) {
      _ppLog_('verify_failed', JSON.stringify({ provider: 'payfast', reason: valid.reason }, null, 2));
      return;
    }

    var status = String(params.payment_status || '').trim().toUpperCase();
    if (status !== 'COMPLETE') {
      _ppLog_('ignored', 'PayFast payment_status not COMPLETE: ' + status);
      return;
    }

    var jobFolderId = String(params.custom_str1 || '').trim();
    var invoiceId = String(params.m_payment_id || params.custom_str2 || '').trim();
    if (!jobFolderId) {
      _ppLog_('error', 'PayFast ITN missing custom_str1 (job folder id). m_payment_id=' + invoiceId);
      return;
    }

    var jobFolder = DriveApp.getFolderById(jobFolderId);

    // Safety: only allow marking folders under our configured root.
    var rootId = String(PropertiesService.getScriptProperties().getProperty('EVIDENCE_ENGINE_ROOT_FOLDER_ID') || '').trim();
    if (rootId && !_ppIsUnderRoot_(jobFolder, rootId)) {
      _ppLog_('error', 'Refusing to mark folder not under root (PayFast). jobFolderId=' + jobFolderId + ' rootId=' + rootId);
      return;
    }

    // Safety: invoice ID match when present.
    if (invoiceId) {
      try {
        var itInv = jobFolder.getFilesByName('INVOICE_ID.txt');
        if (itInv.hasNext()) {
          var invText = String(itInv.next().getBlob().getDataAsString() || '').trim();
          if (invText && invText !== invoiceId) {
            _ppLog_('error', 'PayFast invoice mismatch for folder. expected=' + invText + ' got=' + invoiceId);
            return;
          }
        }
      } catch (_eInv) {}
    }

    // Idempotent.
    try {
      if (jobFolder.getFilesByName('PAID.txt').hasNext()) return;
    } catch (_e0) {}

    var receipt =
      'Paid via PayFast ITN at ' + new Date().toISOString() + '\n' +
      (invoiceId ? ('Invoice ID: ' + invoiceId + '\n') : '') +
      'PayFast payment id: ' + String(params.pf_payment_id || '') + '\n' +
      'Gross: ' + String(params.amount_gross || '') + '\n' +
      'Status: ' + String(params.payment_status || '') + '\n';

    jobFolder.createFile('PAID.txt', receipt, MimeType.PLAIN_TEXT);
    jobFolder.createFile('PAYMENT_RECEIPT.txt', receipt, MimeType.PLAIN_TEXT);
    try {
      jobFolder.createFile('PAYFAST_ITN.txt', JSON.stringify(params, null, 2) + '\n', MimeType.PLAIN_TEXT);
    } catch (_eIt) {}

    _ppLog_('paid', 'PayFast marked paid for jobFolderId=' + jobFolderId + ' invoice=' + invoiceId);
  } catch (err) {
    _ppLog_('exception', 'PayFast handler error: ' + String(err));
  }
}

function _ppFetchOrder_(orderId) {
  var tok = _ppAccessToken_();
  var resp = UrlFetchApp.fetch(_ppApiBase_() + '/v2/checkout/orders/' + encodeURIComponent(String(orderId)), {
    method: 'get',
    headers: {
      Authorization: 'Bearer ' + tok,
      Accept: 'application/json'
    },
    muteHttpExceptions: true
  });
  var code = resp.getResponseCode();
  var text = resp.getContentText();
  if (code < 200 || code >= 300) throw new Error('Get order failed (' + code + '): ' + text);
  return JSON.parse(text);
}

function _ppHandleEvent_(event) {
  var et = String(event && event.event_type ? event.event_type : '').trim();
  if (!et) {
    _ppLog_('ignored', 'Missing event_type');
    return;
  }

  // We mark paid on capture completion.
  if (et !== 'PAYMENT.CAPTURE.COMPLETED') {
    _ppLog_('ignored', 'Ignoring event_type: ' + et);
    return;
  }

  var resource = event.resource || {};
  var related = (resource && resource.supplementary_data && resource.supplementary_data.related_ids) ? resource.supplementary_data.related_ids : {};
  var orderId = String(related.order_id || '').trim();
  if (!orderId) {
    _ppLog_('error', 'CAPTURE event missing related_ids.order_id');
    return;
  }

  // Fetch order to get purchase_units[0].custom_id (we set that to the Drive job folder id).
  var order = _ppFetchOrder_(orderId);
  var orderStatus = String(order && order.status ? order.status : '').trim().toUpperCase();
  if (orderStatus && orderStatus !== 'COMPLETED') {
    _ppLog_('ignored', 'Order not COMPLETED. status=' + orderStatus + ' orderId=' + orderId);
    return;
  }
  var pu = (order && order.purchase_units && order.purchase_units.length) ? order.purchase_units[0] : null;
  var jobFolderId = pu ? String(pu.custom_id || '').trim() : '';
  var invoiceId = pu ? String(pu.invoice_id || '').trim() : '';

  if (!jobFolderId) {
    _ppLog_('error', 'Order missing purchase_units[0].custom_id (job folder id). orderId=' + orderId);
    return;
  }

  var jobFolder = DriveApp.getFolderById(jobFolderId);

  // Safety: only allow marking folders under our configured root.
  var rootId = String(PropertiesService.getScriptProperties().getProperty('EVIDENCE_ENGINE_ROOT_FOLDER_ID') || '').trim();
  if (rootId && !_ppIsUnderRoot_(jobFolder, rootId)) {
    _ppLog_('error', 'Refusing to mark folder not under root. jobFolderId=' + jobFolderId + ' rootId=' + rootId);
    return;
  }

  // Safety: if we have an invoice id, require it to match INVOICE_ID.txt when present.
  if (invoiceId) {
    try {
      var itInv = jobFolder.getFilesByName('INVOICE_ID.txt');
      if (itInv.hasNext()) {
        var invText = String(itInv.next().getBlob().getDataAsString() || '').trim();
        if (invText && invText !== invoiceId) {
          _ppLog_('error', 'Invoice mismatch for folder. expected=' + invText + ' got=' + invoiceId + ' orderId=' + orderId);
          return;
        }
      }
    } catch (_eInv) {
      // ignore
    }
  }

  // Idempotent: if PAID.txt exists, do nothing.
  try {
    if (jobFolder.getFilesByName('PAID.txt').hasNext()) {
      return;
    }
  } catch (_e0) {}

  var payerEmail = '';
  try {
    payerEmail = String(order && order.payer && order.payer.email_address ? order.payer.email_address : '').trim();
  } catch (_e1) {
    payerEmail = '';
  }

  var receipt =
    'Paid via PayPal webhook at ' + new Date().toISOString() + '\n' +
    (invoiceId ? ('Invoice ID: ' + invoiceId + '\n') : '') +
    'Order ID: ' + orderId + '\n' +
    (payerEmail ? ('Payer: ' + payerEmail + '\n') : '');

  jobFolder.createFile('PAID.txt', receipt, MimeType.PLAIN_TEXT);
  jobFolder.createFile('PAYMENT_RECEIPT.txt', receipt, MimeType.PLAIN_TEXT);
  _ppLog_('paid', 'Marked paid for jobFolderId=' + jobFolderId + ' invoice=' + invoiceId + ' order=' + orderId);
}
