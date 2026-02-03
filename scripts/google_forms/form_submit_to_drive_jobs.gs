/**
 * Google Apps Script — "Form → Drive → Local watcher" bridge.
 *
 * What it does:
 * - On each Google Form submission, creates a job folder under a Google Drive root:
 *     EvidenceEngine/
 *       incoming/
 *         <job_name>/
 *           intake.yaml
 *           uploads/   (client uploads go here)
 * - Your Windows machine runs the watcher against the synced local folder (Drive for Desktop).
 *
 * Why this is simplest:
 * - No servers, no web apps, no APIs beyond Apps Script.
 * - Google Form stays the intake UX.
 * - Your local watcher generates the ZIP when uploads are present.
 *
 * Setup:
 * 1) Create a Google Drive folder named "EvidenceEngine".
 * 2) Copy its folder ID into EVIDENCE_ENGINE_ROOT_FOLDER_ID below.
 * 3) Ensure that folder contains subfolders: incoming, processing, done, failed, deliveries.
 * 4) Open your Form → Extensions → Apps Script (or standalone script.google.com).
 * 5) Paste this script, set FORM_ID and ROOT_FOLDER_ID.
 * 6) Run setupTrigger() once and approve permissions.
 */

// REQUIRED: set these.
var FORM_ID = '1FAIpQLSf5aux5aHI0dSbZQHiPQCxX6i--w0BZ-iVC7gONNgla7lqNPw';
var EVIDENCE_ENGINE_ROOT_FOLDER_ID = '1idb7x7NKJF2YrS_DXEqYvkIFMa6JfWa5';

// Optional: customize.
var INCOMING_SUBFOLDER_NAME = 'incoming';

// Optional: payment gating via Stripe Checkout.
// If enabled, the watcher can be configured with REQUIRE_PAYMENT=1 and will wait for PAID.txt.
var ENABLE_STRIPE_CHECKOUT = false;

// Optional: include a PayPal link in the intake email (manual payment, lowest complexity).
// Set PAYPAL_ME_LINK in Script Properties.
var ENABLE_PAYPAL_LINK = true;

// Optional (recommended for South Africa): include a PayFast payment link in the invoice email.
// Script Properties used:
// - PAYFAST_ENV: 'live' (default) or 'sandbox'
// - PAYFAST_MERCHANT_ID
// - PAYFAST_MERCHANT_KEY
// - PAYFAST_PASSPHRASE (optional)
// - PAYFAST_RETURN_URL, PAYFAST_CANCEL_URL (optional)
// - PAYFAST_NOTIFY_URL (recommended; should be your Apps Script Web App URL)
// - PAYFAST_CURRENCY (default ZAR)
// - PAYFAST_AMOUNT (optional; if blank we try to derive from *_AMOUNT_CENTS)
var ENABLE_PAYFAST_LINK = true;

// Optional PayPal tuning via Script Properties:
// - PAYPAL_ME_LINK (required to show PayPal option)
// - PAYPAL_CURRENCY (e.g. USD)
// - PAYPAL_AMOUNT (e.g. 500 or 500.00) OR reuse STRIPE_*_AMOUNT_CENTS to derive an amount
// - PAYPAL_NOTE_HINT (e.g. "Include invoice ID in the note")
// - PAYPAL_ENV: 'live' (default) or 'sandbox'
// - PAYPAL_RETURN_URL, PAYPAL_CANCEL_URL (optional)

// If PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are set, the script will generate a PayPal Checkout
// approval URL per job (preferred; enables reliable webhook matching). Otherwise it falls back to PayPal.me.

// Stripe settings (use Script Properties for secrets where possible).
// Required if ENABLE_STRIPE_CHECKOUT=true.
// - STRIPE_SECRET_KEY (sk_...)
// - STRIPE_CURRENCY (e.g. USD)
// - STRIPE_AMOUNT_CENTS (e.g. 50000)
// - STRIPE_SUCCESS_URL
// - STRIPE_CANCEL_URL

// RECOMMENDED (best automation): add a **File upload** question to your Form.
// Set this to the exact question title.
// Example title: "Upload files (PDF/DOCX/XLSX)"
var FILE_UPLOAD_QUESTION_TITLE = 'Upload files (PDF/DOCX/XLSX)';

// Optional fallback: keep your existing "Upload folder link (Drive/OneDrive)" question.
// If it’s a Google Drive folder link, the script will copy its files into uploads/.
var UPLOAD_FOLDER_LINK_QUESTION_TITLE = 'Upload folder link (Drive/OneDrive)';

function normTitle_(s) {
  return String(s || '')
    .toLowerCase()
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .replace(/\s+/g, ' ');
}

function buildAnswerIndex_(e) {
  var out = {
    respondentEmail: '',
    byNormTitle: {},
    raw: []
  };
  try {
    if (e && e.response) {
      try {
        if (typeof e.response.getRespondentEmail === 'function') {
          out.respondentEmail = String(e.response.getRespondentEmail() || '').trim();
        }
      } catch (errEmail) {}

      var itemResponses = e.response.getItemResponses ? e.response.getItemResponses() : [];
      for (var i = 0; i < itemResponses.length; i++) {
        var ir = itemResponses[i];
        var item = ir.getItem ? ir.getItem() : null;
        var title = item && item.getTitle ? String(item.getTitle() || '') : '';
        var resp = ir.getResponse ? ir.getResponse() : '';
        var norm = normTitle_(title);
        out.raw.push({ title: title, response: resp });
        if (norm) {
          // Avoid clobbering a non-empty answer if the same title appears again (duplicates or edits).
          var existing = out.byNormTitle[norm];
          var existingStr = existing ? first_(existing.response) : '';
          var newStr = first_(resp);
          if (!existing || (!existingStr && newStr)) {
            out.byNormTitle[norm] = { title: title, response: resp };
          }
        }
      }
    }
  } catch (err) {
    Logger.log('buildAnswerIndex_ failed: ' + err);
  }
  return out;
}

function answerByTitles_(index, titles) {
  if (!index) return '';
  for (var i = 0; i < titles.length; i++) {
    var t = String(titles[i] || '');
    var n = normTitle_(t);
    var hit = index.byNormTitle[n];
    if (hit && hit.response != null) {
      return hit.response;
    }

    // Fallback: partial match (handles prefixed titles like "A) Organization" or "Organization (required)").
    try {
      var keys = Object.keys(index.byNormTitle || {});
      for (var k = 0; k < keys.length; k++) {
        var key = keys[k];
        if (!key) continue;
        if (key.indexOf(n) >= 0 || n.indexOf(key) >= 0) {
          var h2 = index.byNormTitle[key];
          if (h2 && h2.response != null && first_(h2.response)) {
            return h2.response;
          }
        }
      }
    } catch (_e) {}
  }
  return '';
}

function answerByTitlesOrNamed_(index, named, titles) {
  var v = answerByTitles_(index, titles);
  var s = first_(v);
  if (s) return s;

  if (named) {
    for (var i = 0; i < titles.length; i++) {
      var key = String(titles[i] || '');
      if (Object.prototype.hasOwnProperty.call(named, key)) {
        var nv = first_(named[key]);
        if (nv) return nv;
      }
    }
  }
  return '';
}

// The email address clients should share their Drive folder with (so this script can copy files).
// Default is evidex.ops@gmail.com; override via Script Property: EVIDEX_OPS_EMAIL
var EVIDEX_OPS_EMAIL = 'evidex.ops@gmail.com';

function getOpsEmail_() {
  var v = _fsGetProp_('EVIDEX_OPS_EMAIL') || EVIDEX_OPS_EMAIL;
  return String(v || '').trim() || 'evidex.ops@gmail.com';
}

function setupTrigger() {
  var formId = _fsRequiredConfig_('FORM_ID', FORM_ID);
  ScriptApp.newTrigger('onFormSubmit')
    .forForm(formId)
    .onFormSubmit()
    .create();
  Logger.log('Trigger created for form: ' + formId);
}

function onFormSubmit(e) {
  var rootFolderId = _fsRequiredConfig_('EVIDENCE_ENGINE_ROOT_FOLDER_ID', EVIDENCE_ENGINE_ROOT_FOLDER_ID);

  var named = (e && e.namedValues) ? e.namedValues : {};
  var idx = buildAnswerIndex_(e);

  var org = answerByTitlesOrNamed_(idx, named, ['Organization', 'Organisation']);
  var capacity = answerByTitlesOrNamed_(idx, named, ['In which capacity or field are you?', 'Capacity / field', 'Your capacity / field']);
  var project = answerByTitlesOrNamed_(idx, named, [
    'Project / engagement name',
    'Grant / project name',
    'Engagement / project name',
    'Reporting pack name'
  ]);
  var periodStart = answerByTitlesOrNamed_(idx, named, ['Reporting period start date', 'Reporting period start']);
  var periodEnd = answerByTitlesOrNamed_(idx, named, ['Reporting period end date', 'Reporting period end']);
  var contactName = answerByTitlesOrNamed_(idx, named, ['Your name', 'Name']);
  var contactEmail = answerByTitlesOrNamed_(idx, named, ['Email for delivery', 'Delivery email', 'Email']);
  if (!contactEmail) contactEmail = String(idx.respondentEmail || '').trim();
  var role = answerByTitlesOrNamed_(idx, named, ['Role']);
  var donor = answerByTitlesOrNamed_(idx, named, [
    'Donor / funder name',
    'Sponsor / funder name',
    'Stakeholder / auditor / regulator name',
    'Stakeholder / funder name (optional)',
    'Stakeholder / funder name'
  ]);
  var purpose = answerByTitlesOrNamed_(idx, named, ['Pack purpose', 'Purpose']);
  var tone = answerByTitlesOrNamed_(idx, named, ['Tone']);
  var grantId = answerByTitlesOrNamed_(idx, named, ['Grant ID (optional)', 'Grant ID']);
  var awardNumber = answerByTitlesOrNamed_(idx, named, ['Award / grant number (optional)', 'Award / grant number']);
  var includeAppendix = answerByTitlesOrNamed_(idx, named, ['Include appendix summaries?', 'Include appendix?']);
  var redFlags = answerByTitlesOrNamed_(idx, named, ['Red flags / known gaps (missing docs, data issues)', 'Red flags / known gaps']);
  var avoidClaims = answerByTitlesOrNamed_(idx, named, ['Risk sensitivities (anything we must avoid stating)', 'Risk sensitivities']);

  // Optional billing tier inputs (recommended for corporate + consultancy automation)
  var clientType = answerByTitlesOrNamed_(idx, named, ['Client type', 'Client type (optional)']);
  var orderSize = answerByTitlesOrNamed_(idx, named, ['Order size (packs)', 'Order size (packs) (optional)']);
  var billingCurrency = answerByTitlesOrNamed_(idx, named, ['Currency', 'Currency (optional)']);
  var orderQty = parseInt_(orderSize, 1);

  // If client type wasn't explicitly chosen, infer it from capacity/purpose/role so pricing automation still works.
  if (!String(clientType || '').trim()) {
    clientType = deriveClientTypeFromCapacityPurposeRole_(capacity, purpose, role);
  }

  // KPI input might be pasted in a single paragraph.
  var pastedKpis = answerByTitlesOrNamed_(idx, named, ['If pasting KPIs: list KPIs (one per line)', 'If pasting KPIs']);

  // Optional: drive/onedrive folder link (fallback)
  var uploadFolderLink = answerByTitlesOrNamed_(idx, named, [UPLOAD_FOLDER_LINK_QUESTION_TITLE]);

  var safeOrg = String(org || '').trim() || 'client';
  var safeProject = String(project || '').trim() || String(purpose || '').trim() || 'evidence_pack';
  var safePeriod = (String(periodStart || '').trim() && String(periodEnd || '').trim()) ? (periodStart + '_to_' + periodEnd) : String(new Date().toISOString().slice(0, 10));
  var jobName = slug_([safeOrg, safeProject, safePeriod].join('__'));

  var root = DriveApp.getFolderById(rootFolderId);
  var incoming = getOrCreateFolder_(root, INCOMING_SUBFOLDER_NAME);

  // If job folder already exists, create a unique suffix.
  var jobFolder = findFolder_(incoming, jobName);
  if (jobFolder) {
    jobName = jobName + '__' + new Date().getTime();
  }
  jobFolder = incoming.createFolder(jobName);
  var uploadsFolder = jobFolder.createFolder('uploads');

  // Persist raw extracted answers for debugging mismatched titles.
  try {
    jobFolder.createFile(
      'FORM_RESPONSE_DEBUG.json',
      JSON.stringify(
        {
          created_at: new Date().toISOString(),
          respondent_email: idx.respondentEmail,
          named_values: named,
          item_responses: idx.raw
        },
        null,
        2
      ),
      MimeType.PLAIN_TEXT
    );
  } catch (errDbg) {
    Logger.log('Could not write FORM_RESPONSE_DEBUG.json: ' + errDbg);
  }

  // Make uploads actually usable: try to grant the client edit access to this per-job folder.
  // If domain sharing rules block this, the email link will still work but may require a manual "Request access".
  try {
    if (contactEmail) {
      uploadsFolder.addEditor(String(contactEmail).trim());
    }
  } catch (errShare) {
    Logger.log('Could not share uploads folder with contact email: ' + errShare);
  }

  // Helpful metadata files so other automations don't need to parse YAML.
  if (contactEmail) jobFolder.createFile('CONTACT_EMAIL.txt', String(contactEmail).trim(), MimeType.PLAIN_TEXT);
  if (contactName) jobFolder.createFile('CONTACT_NAME.txt', String(contactName).trim(), MimeType.PLAIN_TEXT);
  jobFolder.createFile('JOB_NAME.txt', String(jobName), MimeType.PLAIN_TEXT);

  var intakeYaml = buildIntakeYaml_({
    client: {
      organization: org,
      contact_name: contactName,
      contact_email: contactEmail,
      capacity: capacity
    },
    pack: {
      purpose: purpose,
      donor: donor,
      project_name: project,
      grant_id: grantId || awardNumber,
      reporting_period: { start: periodStart, end: periodEnd },
      tone: tone || 'neutral',
      include_appendix: (String(includeAppendix).toLowerCase() === 'yes')
    },
    billing: {
      client_type: clientType,
      quantity: orderQty,
      currency: billingCurrency
    },
    kpis: parsePastedKpis_(pastedKpis),
    constraints: {
      avoid_claims: avoidClaims ? [avoidClaims] : [],
      known_gaps: redFlags ? [redFlags] : []
    }
  });

  jobFolder.createFile('intake.yaml', intakeYaml, MimeType.PLAIN_TEXT);

  // --- Attach uploads automatically ---
  // Best path: File Upload question -> file IDs -> move into this job's uploads folder.
  var uploadedFileIds = extractUploadedFileIds_(e, FILE_UPLOAD_QUESTION_TITLE);
  if (uploadedFileIds.length) {
    for (var i = 0; i < uploadedFileIds.length; i++) {
      try {
        var f = DriveApp.getFileById(String(uploadedFileIds[i]));
        // Move (not copy) so the job folder is the single source of truth.
        f.moveTo(uploadsFolder);
      } catch (errMove) {
        Logger.log('Could not move uploaded file ID ' + uploadedFileIds[i] + ': ' + errMove);
      }
    }
  } else {
    // Fallback: if a Google Drive folder link was provided, copy files from there.
    var driveFolderId = extractDriveFolderIdFromUrl_(uploadFolderLink);
    if (driveFolderId) {
      try {
        var srcFolder = DriveApp.getFolderById(driveFolderId);
        copyFolderFilesRecursive_(srcFolder, uploadsFolder, 2);
      } catch (errCopy) {
        Logger.log('Could not copy from provided Drive folder: ' + errCopy);
        jobFolder.createFile(
          'UPLOADS_ACTION_REQUIRED.txt',
          'Could not copy uploads from the provided link.\n' +
            'Make sure it is a Google Drive folder link and that this account has access.\n' +
            'Provided link: ' + uploadFolderLink + '\n',
          MimeType.PLAIN_TEXT
        );
      }
    } else {
      jobFolder.createFile(
        'UPLOADS_ACTION_REQUIRED.txt',
        'No files were attached via the Form.\n\n' +
          'Upload your source files into this job\'s uploads folder (link is emailed to the delivery address if provided).\n\n' +
          'If you did NOT receive the email, you can still upload by sharing a Google Drive folder link (not OneDrive) in the field:\n' +
          UPLOAD_FOLDER_LINK_QUESTION_TITLE + '\n\n' +
          'If you share a Drive folder link, share it with: ' + getOpsEmail_() + '\n\n' +
          'Note: Some Google accounts/domains do not support Google Forms “File upload” questions.\n',
        MimeType.PLAIN_TEXT
      );
    }
  }

  // Help the client: send them the uploads folder link (optional).
  // NOTE: external sharing rules may apply in Workspace domains.
  try {
    var toEmail = String(contactEmail || '').trim() || String(idx.respondentEmail || '').trim();
    if (toEmail) {
      var uploadsUrl = uploadsFolder.getUrl();
      var jobUrl = jobFolder.getUrl();

      // Build a simple invoice ID for tracking.
      var tz = Session.getScriptTimeZone ? Session.getScriptTimeZone() : 'UTC';
      var stamp = Utilities.formatDate(new Date(), tz, 'yyyyMMdd');
      var invoiceId = 'EVIDEX-' + stamp + '-' + jobName;

      // Optional: include a Stripe payment link.
      var paymentUrl = '';
      if (ENABLE_STRIPE_CHECKOUT) {
        try {
          paymentUrl = createCheckoutSessionUrl_(jobName, jobFolder.getId(), toEmail, clientType, orderQty);
          jobFolder.createFile('PAYMENT_URL.txt', paymentUrl, MimeType.PLAIN_TEXT);
        } catch (errPay) {
          Logger.log('Stripe checkout link creation failed: ' + errPay);
        }
      }

      // Optional: PayPal link (manual payment).
      var paypalUrl = '';
      if (ENABLE_PAYPAL_LINK) {
        paypalUrl = _fsGetProp_('PAYPAL_ME_LINK') || '';
        paypalUrl = String(paypalUrl || '').trim();
      }

      // Best-effort: derive a PayPal amount from script properties (or Stripe cents tiers if present).
      var paypalCurrency = String(_fsGetProp_('PAYPAL_CURRENCY') || _fsGetProp_('STRIPE_CURRENCY') || billingCurrency || 'USD').trim() || 'USD';
      var paypalAmount = String(_fsGetProp_('PAYPAL_AMOUNT') || '').trim();
      if (!paypalAmount) {
        // Reuse tiered cents if available.
        var cents = '';
        if (String(clientType || '').toLowerCase().indexOf('corporate') >= 0) {
          cents = _fsGetProp_('CORPORATE_AMOUNT_CENTS') || '';
        } else if (String(clientType || '').toLowerCase().indexOf('consult') >= 0) {
          cents = _fsGetProp_('CONSULTANCY_AMOUNT_CENTS') || '';
        } else {
          cents = _fsGetProp_('NGO_AMOUNT_CENTS') || '';
        }
        cents = String(cents || _fsGetProp_('STRIPE_AMOUNT_CENTS') || '').trim();
        var nCents = parseInt_(cents, 0);
        if (nCents > 0) {
          paypalAmount = String((nCents / 100.0).toFixed(2));
        }
      }

      // If this looks like a PayPal.me URL and an amount was derived, append /<amount>.
      var paypalPayUrl = paypalUrl;
      if (paypalPayUrl && paypalAmount) {
        var lower = paypalPayUrl.toLowerCase();
        if (lower.indexOf('paypal.me/') >= 0) {
          // Avoid double-appending if an amount is already present.
          if (!/paypal\.me\/.+\/[0-9]+(\.[0-9]+)?\/?$/i.test(paypalPayUrl)) {
            paypalPayUrl = paypalPayUrl.replace(/\/+$/, '') + '/' + paypalAmount;
          }
        }
      }

      // Preferred: create a PayPal Checkout order approval URL (requires PayPal API credentials).
      // We set purchase_units[0].custom_id = jobFolderId so webhook can mark the right folder as paid.
      var paypalApprovalUrl = '';
      var paypalOrderId = '';
      try {
        if (_fsGetProp_('PAYPAL_CLIENT_ID') && _fsGetProp_('PAYPAL_CLIENT_SECRET') && paypalAmount) {
          var created = createPayPalApprovalUrl_(jobName, jobFolder.getId(), invoiceId, paypalCurrency, paypalAmount);
          paypalApprovalUrl = String(created && created.approvalUrl ? created.approvalUrl : '').trim();
          paypalOrderId = String(created && created.orderId ? created.orderId : '').trim();
          if (paypalApprovalUrl) {
            jobFolder.createFile('PAYPAL_APPROVAL_URL.txt', paypalApprovalUrl, MimeType.PLAIN_TEXT);
          }
          if (paypalOrderId) {
            jobFolder.createFile('PAYPAL_ORDER_ID.txt', paypalOrderId, MimeType.PLAIN_TEXT);
          }
        }
      } catch (errPp) {
        Logger.log('PayPal approval URL creation failed: ' + errPp);
      }

      // Optional: PayFast payment link (recommended for South Africa).
      var payfastPayUrl = '';
      var payfastMerchantId = '';
      try {
        if (ENABLE_PAYFAST_LINK) {
          payfastMerchantId = String(_fsGetProp_('PAYFAST_MERCHANT_ID') || '').trim();
          var payfastMerchantKey = String(_fsGetProp_('PAYFAST_MERCHANT_KEY') || '').trim();
          if (payfastMerchantId && payfastMerchantKey) {
            var payfastCurrency = String(_fsGetProp_('PAYFAST_CURRENCY') || billingCurrency || 'ZAR').trim() || 'ZAR';
            var payfastAmount = String(_fsGetProp_('PAYFAST_AMOUNT') || '').trim();
            if (!payfastAmount) {
              // Reuse tiered cents if available.
              var pfCents = '';
              if (String(clientType || '').toLowerCase().indexOf('corporate') >= 0) {
                pfCents = _fsGetProp_('CORPORATE_AMOUNT_CENTS') || '';
              } else if (String(clientType || '').toLowerCase().indexOf('consult') >= 0) {
                pfCents = _fsGetProp_('CONSULTANCY_AMOUNT_CENTS') || '';
              } else {
                pfCents = _fsGetProp_('NGO_AMOUNT_CENTS') || '';
              }
              pfCents = String(pfCents || _fsGetProp_('STRIPE_AMOUNT_CENTS') || '').trim();
              var pfNCents = parseInt_(pfCents, 0);
              if (pfNCents > 0) {
                payfastAmount = String((pfNCents / 100.0).toFixed(2));
              }
            }

            if (payfastAmount) {
              payfastPayUrl = createPayFastPaymentUrl_(jobName, jobFolder.getId(), invoiceId, payfastCurrency, payfastAmount, toEmail);
              if (payfastPayUrl) {
                try { jobFolder.createFile('PAYFAST_URL.txt', payfastPayUrl, MimeType.PLAIN_TEXT); } catch (_pf1) {}
                try { jobFolder.createFile('PAYFAST_M_PAYMENT_ID.txt', invoiceId, MimeType.PLAIN_TEXT); } catch (_pf2) {}
              }
            }
          }
        }
      } catch (errPf) {
        Logger.log('PayFast link creation failed: ' + errPf);
      }

      // Persist invoice/payment metadata into the job folder for downstream tooling.
      try { jobFolder.createFile('INVOICE_ID.txt', invoiceId, MimeType.PLAIN_TEXT); } catch (_e1) {}
      try {
        jobFolder.createFile(
          'INVOICE.txt',
          'Invoice ID: ' + invoiceId + '\n' +
            'Job: ' + jobName + '\n' +
            'Client type: ' + clientType + '\n' +
            'Quantity: ' + String(orderQty) + '\n' +
            (paypalAmount ? ('PayPal amount: ' + paypalCurrency + ' ' + paypalAmount + '\n') : '') +
            (paymentUrl ? ('Payment URL: ' + paymentUrl + '\n') : '') +
            (payfastPayUrl ? ('PayFast URL: ' + payfastPayUrl + '\n') : '') +
            (paypalApprovalUrl ? ('PayPal approval URL: ' + paypalApprovalUrl + '\n') : '') +
            (paypalPayUrl ? ('PayPal.me URL: ' + paypalPayUrl + '\n') : ''),
          MimeType.PLAIN_TEXT
        );
      } catch (_e2) {}

      if (paypalUrl || paypalApprovalUrl) {
        try { jobFolder.createFile('PAYPAL_URL.txt', paypalApprovalUrl || paypalPayUrl || paypalUrl, MimeType.PLAIN_TEXT); } catch (_e3) {}
      }

      // Stage A: Invoice / payment request email (idempotent via INVOICE_SENT.txt)
      var noteHint = String(_fsGetProp_('PAYPAL_NOTE_HINT') || 'In the PayPal note, include this invoice ID: ' + invoiceId).trim();
      var alreadyInvoiced = false;
      try {
        alreadyInvoiced = jobFolder.getFilesByName('INVOICE_SENT.txt').hasNext();
      } catch (_e4) {
        alreadyInvoiced = false;
      }

      if (!alreadyInvoiced) {
        var subject = 'EVIDEX — Invoice / payment + uploads (' + jobName + ')';
        var body =
          'Thanks — your EVIDEX intake is received.\n\n' +
          'Invoice ID: ' + invoiceId + '\n\n' +
          '1) Upload your source files (and share access if required):\n' +
          uploadsUrl + '\n\n' +
          'Job folder (for reference):\n' +
          jobUrl + '\n\n' +
          '2) Payment options:\n' +
          (paymentUrl ? ('- Card (Stripe): ' + paymentUrl + '\n') : '') +
          (payfastPayUrl ? ('- PayFast (South Africa): ' + payfastPayUrl + '\n') : '') +
          (paypalApprovalUrl
            ? ('- PayPal (secure checkout): ' + paypalApprovalUrl + (paypalAmount ? ('  (' + paypalCurrency + ' ' + paypalAmount + ')') : '') + '\n')
            : (paypalPayUrl
              ? ('- PayPal: ' + paypalPayUrl + (paypalAmount ? ('  (' + paypalCurrency + ' ' + paypalAmount + ')') : '') + '\n')
              : (paypalUrl ? ('- PayPal: ' + paypalUrl + '\n') : '')
            )) +
          (paypalUrl ? ('\n' + noteHint + '\n') : '') +
          '\nOnce files are uploaded' + ((paymentUrl || paypalUrl) ? ' and payment is confirmed' : '') + ', your evidence pack will be generated automatically.\n';

        MailApp.sendEmail({
          to: toEmail,
          subject: subject,
          body: body
        });

        try {
          jobFolder.createFile('INVOICE_SENT.txt', 'Sent at ' + new Date().toISOString() + '\nTo: ' + toEmail + '\n', MimeType.PLAIN_TEXT);
        } catch (_e5) {}
      }
    }
  } catch (err) {
    Logger.log('Email send skipped/failed: ' + err);
  }

  Logger.log('Created job: ' + jobName + ' at ' + jobFolder.getUrl());
}

function _fsEnv_(name, defaultValue) {
  var v = PropertiesService.getScriptProperties().getProperty(String(name));
  v = String(v || '').trim();
  if (!v) v = String(defaultValue || '').trim();
  return v;
}

function _fsPayPalEnv_() {
  var env = String(_fsGetProp_('PAYPAL_ENV') || 'live').trim().toLowerCase();
  if (env !== 'sandbox') env = 'live';
  return env;
}

function _fsPayFastEnv_() {
  var env = String(_fsGetProp_('PAYFAST_ENV') || 'live').trim().toLowerCase();
  if (env !== 'sandbox') env = 'live';
  return env;
}

function _fsPayFastProcessUrl_() {
  return _fsPayFastEnv_() === 'sandbox'
    ? 'https://sandbox.payfast.co.za/eng/process'
    : 'https://www.payfast.co.za/eng/process';
}

function _fsPayFastUrlEncode_(s) {
  // Match common PayFast examples (PHP urlencode-like): space as +, encode *.
  return encodeURIComponent(String(s || ''))
    .replace(/%20/g, '+')
    .replace(/\*/g, '%2A');
}

function _fsPayFastMd5Hex_(text) {
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

function createPayFastPaymentUrl_(jobName, jobFolderId, invoiceId, currency, amount, buyerEmail) {
  var merchantId = String(_fsRequireProp_('PAYFAST_MERCHANT_ID') || '').trim();
  var merchantKey = String(_fsRequireProp_('PAYFAST_MERCHANT_KEY') || '').trim();
  var passphrase = String(_fsGetProp_('PAYFAST_PASSPHRASE') || '').trim();

  var returnUrl = String(_fsGetProp_('PAYFAST_RETURN_URL') || '').trim();
  var cancelUrl = String(_fsGetProp_('PAYFAST_CANCEL_URL') || '').trim();
  var notifyUrl = String(_fsGetProp_('PAYFAST_NOTIFY_URL') || '').trim();
  // notify_url is strongly recommended; without it you won't get ITNs.

  var data = {
    merchant_id: merchantId,
    merchant_key: merchantKey,
    return_url: returnUrl,
    cancel_url: cancelUrl,
    notify_url: notifyUrl,
    m_payment_id: String(invoiceId || ''),
    amount: String(amount || ''),
    item_name: 'EVIDEX Evidence Pack — ' + String(jobName || ''),
    // custom_str1 will be used by our webhook/ITN receiver to locate the job folder.
    custom_str1: String(jobFolderId || ''),
    custom_str2: String(invoiceId || ''),
    email_address: String(buyerEmail || '')
  };

  // Optional currency parameter (PayFast is primarily ZAR; keep configurable).
  var ccy = String(currency || '').trim();
  if (ccy) data.currency = ccy;

  // Build param string (sorted for determinism).
  var keys = [];
  for (var k in data) {
    if (!data.hasOwnProperty(k)) continue;
    var v = String(data[k] || '').trim();
    if (!v) continue;
    keys.push(k);
  }
  keys.sort();

  var pairs = [];
  for (var i = 0; i < keys.length; i++) {
    var key = keys[i];
    var val = String(data[key] || '').trim();
    pairs.push(key + '=' + _fsPayFastUrlEncode_(val));
  }
  var paramString = pairs.join('&');

  // PayFast signature for the payment request (optional but recommended).
  var sigString = paramString;
  if (passphrase) {
    sigString += '&passphrase=' + _fsPayFastUrlEncode_(passphrase);
  }
  var signature = _fsPayFastMd5Hex_(sigString);

  return _fsPayFastProcessUrl_() + '?' + paramString + '&signature=' + signature;
}

function _fsPayPalApiBase_() {
  return _fsPayPalEnv_() === 'sandbox' ? 'https://api-m.sandbox.paypal.com' : 'https://api-m.paypal.com';
}

function _fsPayPalAccessToken_() {
  var cache = CacheService.getScriptCache();
  var cached = cache.get('pp_access_token');
  if (cached) return String(cached);

  var clientId = _fsRequireProp_('PAYPAL_CLIENT_ID');
  var secret = _fsRequireProp_('PAYPAL_CLIENT_SECRET');
  var basic = Utilities.base64Encode(clientId + ':' + secret);

  var resp = UrlFetchApp.fetch(_fsPayPalApiBase_() + '/v1/oauth2/token', {
    method: 'post',
    payload: { grant_type: 'client_credentials' },
    headers: { Authorization: 'Basic ' + basic, Accept: 'application/json' },
    muteHttpExceptions: true
  });
  var code = resp.getResponseCode();
  var text = resp.getContentText();
  if (code < 200 || code >= 300) throw new Error('PayPal token failed (' + code + '): ' + text);
  var data = JSON.parse(text);
  var tok = String(data && data.access_token ? data.access_token : '');
  if (!tok) throw new Error('PayPal token missing access_token');
  try { cache.put('pp_access_token', tok, 50 * 60); } catch (_e) {}
  return tok;
}

function createPayPalApprovalUrl_(jobName, jobFolderId, invoiceId, currency, amount) {
  var tok = _fsPayPalAccessToken_();
  var returnUrl = String(_fsGetProp_('PAYPAL_RETURN_URL') || '').trim();
  var cancelUrl = String(_fsGetProp_('PAYPAL_CANCEL_URL') || '').trim();
  if (!returnUrl) returnUrl = 'https://www.paypal.com/';
  if (!cancelUrl) cancelUrl = 'https://www.paypal.com/';

  var payload = {
    intent: 'CAPTURE',
    purchase_units: [
      {
        custom_id: String(jobFolderId || ''),
        invoice_id: String(invoiceId || ''),
        description: 'EVIDEX Evidence Pack — ' + String(jobName || ''),
        amount: {
          currency_code: String(currency || 'USD'),
          value: String(amount)
        }
      }
    ],
    application_context: {
      brand_name: 'EVIDEX',
      landing_page: 'BILLING',
      user_action: 'PAY_NOW',
      return_url: returnUrl,
      cancel_url: cancelUrl
    }
  };

  var resp = UrlFetchApp.fetch(_fsPayPalApiBase_() + '/v2/checkout/orders', {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    headers: { Authorization: 'Bearer ' + tok, Accept: 'application/json' },
    muteHttpExceptions: true
  });

  var code = resp.getResponseCode();
  var text = resp.getContentText();
  if (code < 200 || code >= 300) throw new Error('PayPal order create failed (' + code + '): ' + text);
  var data = JSON.parse(text);
  var orderId = String(data && data.id ? data.id : '');
  var approvalUrl = '';
  try {
    var links = data && data.links ? data.links : [];
    for (var i = 0; i < links.length; i++) {
      if (String(links[i].rel || '').toLowerCase() === 'approve') {
        approvalUrl = String(links[i].href || '');
        break;
      }
    }
  } catch (_e2) {}
  if (!approvalUrl) throw new Error('PayPal response missing approval URL');
  return { orderId: orderId, approvalUrl: approvalUrl };
}

function deriveClientTypeFromCapacityPurposeRole_(capacity, purpose, role) {
  var c = String(capacity || '').toLowerCase();
  if (c.indexOf('consult') >= 0 || c.indexOf('agency') >= 0) return 'Consultancy';
  if (c.indexOf('corporate') >= 0 || c.indexOf('audit') >= 0 || c.indexOf('compliance') >= 0 || c.indexOf('esg') >= 0) return 'Corporate';
  if (c.indexOf('university') >= 0 || c.indexOf('research') >= 0 || c.indexOf('sponsor') >= 0) return 'NGO/grant-funded';
  if (c.indexOf('grant') >= 0 || c.indexOf('ngo') >= 0 || c.indexOf('donor') >= 0) return 'NGO/grant-funded';
  return deriveClientTypeFromPurposeAndRole_(purpose, role);
}

function deriveClientTypeFromPurposeAndRole_(purpose, role) {
  var p = String(purpose || '').toLowerCase();
  var r = String(role || '').toLowerCase();

  // If the submitter is a consultant, default to consultancy.
  if (r.indexOf('consult') >= 0) return 'Consultancy';

  // Purpose-driven defaults (seasonal urgency offers)
  if (p.indexOf('tax') >= 0 || p.indexOf('vat') >= 0) return 'Corporate';
  if (p.indexOf('audit') >= 0) return 'Corporate';
  if (p.indexOf('internal') >= 0 || p.indexOf('controls') >= 0 || p.indexOf('compliance') >= 0) return 'Corporate';
  if (p.indexOf('esg') >= 0 || p.indexOf('csi') >= 0) return 'Corporate';
  if (p.indexOf('university') >= 0 || p.indexOf('research') >= 0) return 'Corporate';

  // Donor reporting typically indicates NGO/grant-funded.
  if (p.indexOf('donor') >= 0 || p.indexOf('grant') >= 0 || p.indexOf('funder') >= 0) return 'NGO/grant-funded';

  // Safe default
  return 'Corporate';
}

function _fsGetProp_(name) {
  var v = PropertiesService.getScriptProperties().getProperty(name);
  return String(v || '').trim();
}

function _fsRequireProp_(name) {
  var v = _fsGetProp_(name);
  if (!v) throw new Error('Missing Script Property: ' + name);
  return v;
}

function _fsRequiredConfig_(name, fallbackValue) {
  var v = String(fallbackValue || '').trim();
  if (!v) v = _fsGetProp_(name);
  if (!v) throw new Error('Missing config: ' + name + ' (set Script Property or top-level var)');
  return v;
}

// Minimal Stripe Checkout session creator (no server). Uses UrlFetchApp to call Stripe API.
function createCheckoutSessionUrl_(jobName, jobFolderId, customerEmail) {
  var secretKey = _fsRequireProp_('STRIPE_SECRET_KEY');

  var currency = _fsGetProp_('STRIPE_CURRENCY') || 'USD';
  var amountCents = _fsGetProp_('STRIPE_AMOUNT_CENTS') || '50000';
  var successUrl = _fsRequireProp_('STRIPE_SUCCESS_URL');
  var cancelUrl = _fsRequireProp_('STRIPE_CANCEL_URL');

  // Optional tiered pricing via Script Properties.
  // If set, these override STRIPE_AMOUNT_CENTS.
  // - NGO_AMOUNT_CENTS (e.g. 50000)
  // - CORPORATE_AMOUNT_CENTS (e.g. 90000)
  // - CONSULTANCY_AMOUNT_CENTS (e.g. 40000)
  // Note: quantity is handled via Stripe line_items quantity.
  var tier = String(arguments.length >= 4 ? arguments[3] : '').trim().toLowerCase();
  var qty = (arguments.length >= 5 ? arguments[4] : 1);
  if (!qty || qty < 1) qty = 1;

  var productName = _fsGetProp_('STRIPE_PRODUCT_NAME') || 'EVIDEX — Evidence & Compliance Pack (48-Hour Delivery)';
  if (tier) {
    if (tier === 'corporate' || tier === 'company' || tier === 'enterprise') {
      amountCents = _fsGetProp_('CORPORATE_AMOUNT_CENTS') || amountCents;
      productName = _fsGetProp_('CORPORATE_PRODUCT_NAME') || productName;
    } else if (tier === 'consultancy' || tier === 'consultant' || tier === 'agency' || tier === 'partner' || tier === 'reseller') {
      amountCents = _fsGetProp_('CONSULTANCY_AMOUNT_CENTS') || amountCents;
      productName = _fsGetProp_('CONSULTANCY_PRODUCT_NAME') || productName;
    } else {
      amountCents = _fsGetProp_('NGO_AMOUNT_CENTS') || amountCents;
      productName = _fsGetProp_('NGO_PRODUCT_NAME') || productName;
    }
  }

  var payload = {
    'mode': 'payment',
    'success_url': successUrl,
    'cancel_url': cancelUrl,
    'client_reference_id': jobName,
    'metadata[job_folder_id]': jobFolderId,
    'metadata[job_name]': jobName,
    'line_items[0][quantity]': String(qty),
    'line_items[0][price_data][currency]': currency,
    'line_items[0][price_data][unit_amount]': String(amountCents),
    'line_items[0][price_data][product_data][name]': productName
  };
  if (customerEmail) payload['customer_email'] = customerEmail;

  var resp = UrlFetchApp.fetch('https://api.stripe.com/v1/checkout/sessions', {
    method: 'post',
    payload: payload,
    headers: { Authorization: 'Bearer ' + secretKey },
    muteHttpExceptions: true
  });

  var code = resp.getResponseCode();
  var text = resp.getContentText();
  if (code < 200 || code >= 300) throw new Error('Stripe session create failed (' + code + '): ' + text);
  var data = JSON.parse(text);
  if (!data || !data.url) throw new Error('Stripe response missing checkout URL');
  return data.url;
}

function parseInt_(v, defaultValue) {
  var s = String(v || '').trim();
  if (!s) return defaultValue;
  var n = parseInt(s, 10);
  return (isNaN(n) || !isFinite(n)) ? defaultValue : n;
}

function pickNamedValue_(named, titles) {
  if (!named) return '';
  for (var i = 0; i < titles.length; i++) {
    var t = titles[i];
    if (Object.prototype.hasOwnProperty.call(named, t) && named[t]) return named[t];
  }
  return '';
}

function extractUploadedFileIds_(e, questionTitle) {
  try {
    if (!e || !e.response) return [];
    var itemResponses = e.response.getItemResponses();
    var out = [];
    for (var i = 0; i < itemResponses.length; i++) {
      var ir = itemResponses[i];
      var item = ir.getItem();
      if (!item || item.getTitle() !== questionTitle) continue;
      var resp = ir.getResponse();
      if (!resp) continue;
      if (Array.isArray(resp)) {
        for (var j = 0; j < resp.length; j++) out.push(String(resp[j]));
      } else {
        out.push(String(resp));
      }
    }
    return out;
  } catch (err) {
    Logger.log('extractUploadedFileIds_ failed: ' + err);
    return [];
  }
}

function extractDriveFolderIdFromUrl_(url) {
  var u = String(url || '').trim();
  if (!u) return '';
  // Typical folder URL: https://drive.google.com/drive/folders/<ID>
  var m1 = u.match(/drive\.google\.com\/drive\/folders\/([^\?\/#]+)/i);
  if (m1 && m1[1]) return m1[1];
  // Alternate: https://drive.google.com/open?id=<ID>
  var m2 = u.match(/[\?&]id=([^\?\/#&]+)/i);
  if (m2 && m2[1]) return m2[1];
  return '';
}

function copyFolderFilesRecursive_(srcFolder, destFolder, maxDepth) {
  var depth = (maxDepth == null) ? 1 : maxDepth;
  // Copy files in this folder
  var files = srcFolder.getFiles();
  while (files.hasNext()) {
    var f = files.next();
    try {
      f.makeCopy(f.getName(), destFolder);
    } catch (errCopyFile) {
      Logger.log('Could not copy file ' + f.getId() + ': ' + errCopyFile);
    }
  }
  if (depth <= 0) return;

  // Recurse into subfolders up to maxDepth
  var subs = srcFolder.getFolders();
  while (subs.hasNext()) {
    var sf = subs.next();
    var child = destFolder.createFolder(sf.getName());
    copyFolderFilesRecursive_(sf, child, depth - 1);
  }
}

function first_(v) {
  if (!v) return '';
  if (Array.isArray(v)) return v.length ? String(v[0]).trim() : '';
  return String(v).trim();
}

function slug_(s) {
  return String(s || '')
    .trim()
    .replace(/\s+/g, '_')
    .replace(/[^A-Za-z0-9_\-\.]/g, '_')
    .replace(/_+/g, '_')
    .slice(0, 120);
}

function getOrCreateFolder_(parent, name) {
  var it = parent.getFoldersByName(name);
  if (it.hasNext()) return it.next();
  return parent.createFolder(name);
}

function findFolder_(parent, name) {
  var it = parent.getFoldersByName(name);
  return it.hasNext() ? it.next() : null;
}

function parsePastedKpis_(text) {
  var t = String(text || '').trim();
  if (!t) return [];

  var lines = t.split(/\r?\n/).map(function (l) { return l.trim(); }).filter(Boolean);
  var kpis = [];
  for (var i = 0; i < lines.length; i++) {
    // Expected: KPI name | target | actual | how measured
    var parts = lines[i].split('|').map(function (p) { return p.trim(); });
    kpis.push({
      name: parts[0] || lines[i],
      target: parts[1] || '',
      actual: parts[2] || '',
      measurement: parts[3] || ''
    });
  }
  return kpis;
}

function buildIntakeYaml_(obj) {
  // Minimal YAML serializer for our fixed schema.
  function q(v) {
    var s = String(v == null ? '' : v);
    // Quote if it contains special characters.
    if (s === '' || /[:#\n\r\t]/.test(s) || /^\s|\s$/.test(s)) {
      return '"' + s.replace(/\\/g, '\\\\').replace(/\"/g, '\\"') + '"';
    }
    return '"' + s.replace(/\\/g, '\\\\').replace(/\"/g, '\\"') + '"';
  }

  var out = [];
  out.push('client:');
  out.push('  organization: ' + q(obj.client.organization));
  out.push('  contact_name: ' + q(obj.client.contact_name));
  out.push('  contact_email: ' + q(obj.client.contact_email));
  out.push('');

  out.push('pack:');
  out.push('  purpose: ' + q(obj.pack.purpose));
  out.push('  donor: ' + q(obj.pack.donor));
  out.push('  project_name: ' + q(obj.pack.project_name));
  out.push('  grant_id: ' + q(obj.pack.grant_id));
  out.push('  reporting_period:');
  out.push('    start: ' + q(obj.pack.reporting_period.start));
  out.push('    end: ' + q(obj.pack.reporting_period.end));
  out.push('  tone: ' + q(obj.pack.tone));
  out.push('  include_appendix: ' + (obj.pack.include_appendix ? 'true' : 'false'));
  out.push('');

  out.push('kpis:');
  if (obj.kpis && obj.kpis.length) {
    for (var i = 0; i < obj.kpis.length; i++) {
      out.push('  - name: ' + q(obj.kpis[i].name));
      out.push('    target: ' + q(obj.kpis[i].target));
      out.push('    actual: ' + q(obj.kpis[i].actual));
      out.push('    measurement: ' + q(obj.kpis[i].measurement));
    }
  } else {
    out.push('  []');
  }
  out.push('');

  out.push('constraints:');
  out.push('  avoid_claims:');
  if (obj.constraints.avoid_claims && obj.constraints.avoid_claims.length) {
    for (var a = 0; a < obj.constraints.avoid_claims.length; a++) {
      out.push('    - ' + q(obj.constraints.avoid_claims[a]));
    }
  } else {
    out.push('    []');
  }

  out.push('  known_gaps:');
  if (obj.constraints.known_gaps && obj.constraints.known_gaps.length) {
    for (var g = 0; g < obj.constraints.known_gaps.length; g++) {
      out.push('    - ' + q(obj.constraints.known_gaps[g]));
    }
  } else {
    out.push('    []');
  }

  out.push('');

  // Optional billing: only write non-empty fields to avoid invalid types like "" for floats.
  function hasVal_(v) {
    if (v == null) return false;
    var s = String(v).trim();
    return s !== '';
  }
  if (obj.billing && (hasVal_(obj.billing.client_type) || hasVal_(obj.billing.quantity) || hasVal_(obj.billing.unit_price) || hasVal_(obj.billing.currency) || hasVal_(obj.billing.service_name))) {
    out.push('billing:');
    if (hasVal_(obj.billing.client_type)) out.push('  client_type: ' + q(obj.billing.client_type));
    if (hasVal_(obj.billing.quantity)) out.push('  quantity: ' + q(obj.billing.quantity));
    if (hasVal_(obj.billing.unit_price)) out.push('  unit_price: ' + q(obj.billing.unit_price));
    if (hasVal_(obj.billing.currency)) out.push('  currency: ' + q(obj.billing.currency));
    if (hasVal_(obj.billing.service_name)) out.push('  service_name: ' + q(obj.billing.service_name));
    out.push('');
  }

  return out.join('\n');
}
