/**
 * EVIDEX config helper
 *
 * Lets the desktop UI (via clasp) set Script Properties without manual editing.
 */

function evidexConfigure(formId, evidenceEngineRootFolderId) {
  var props = PropertiesService.getScriptProperties();

  var form = String(formId || '').trim();
  var root = String(evidenceEngineRootFolderId || '').trim();

  if (form) {
    props.setProperty('FORM_ID', form);
  }
  if (root) {
    props.setProperty('EVIDENCE_ENGINE_ROOT_FOLDER_ID', root);
  }

  return {
    ok: true,
    FORM_ID: props.getProperty('FORM_ID') || '',
    EVIDENCE_ENGINE_ROOT_FOLDER_ID: props.getProperty('EVIDENCE_ENGINE_ROOT_FOLDER_ID') || ''
  };
}

function evidexShowConfig() {
  var props = PropertiesService.getScriptProperties();
  return {
    FORM_ID: props.getProperty('FORM_ID') || '',
    EVIDENCE_ENGINE_ROOT_FOLDER_ID: props.getProperty('EVIDENCE_ENGINE_ROOT_FOLDER_ID') || ''
  };
}

function evidexConfigurePayments(requirePayment, paypalMeLink, paypalCurrency, paypalAmount) {
  var props = PropertiesService.getScriptProperties();

  var req = String(requirePayment || '').trim();
  if (req) {
    props.setProperty('REQUIRE_PAYMENT', req);
  }

  var link = String(paypalMeLink || '').trim();
  if (link) {
    props.setProperty('PAYPAL_ME_LINK', link);
  }

  var cur = String(paypalCurrency || '').trim();
  if (cur) {
    props.setProperty('PAYPAL_CURRENCY', cur);
  }

  var amt = String(paypalAmount || '').trim();
  if (amt) {
    props.setProperty('PAYPAL_AMOUNT', amt);
  }

  return {
    ok: true,
    REQUIRE_PAYMENT: props.getProperty('REQUIRE_PAYMENT') || '',
    PAYPAL_ME_LINK: props.getProperty('PAYPAL_ME_LINK') || '',
    PAYPAL_CURRENCY: props.getProperty('PAYPAL_CURRENCY') || '',
    PAYPAL_AMOUNT: props.getProperty('PAYPAL_AMOUNT') || ''
  };
}

function evidexConfigurePayPalApi(paypalEnv, paypalClientId, paypalClientSecret, paypalWebhookId, paypalReturnUrl, paypalCancelUrl) {
  var props = PropertiesService.getScriptProperties();

  var env = String(paypalEnv || '').trim();
  if (env) props.setProperty('PAYPAL_ENV', env);

  var cid = String(paypalClientId || '').trim();
  if (cid) props.setProperty('PAYPAL_CLIENT_ID', cid);

  var sec = String(paypalClientSecret || '').trim();
  if (sec) props.setProperty('PAYPAL_CLIENT_SECRET', sec);

  var wid = String(paypalWebhookId || '').trim();
  if (wid) props.setProperty('PAYPAL_WEBHOOK_ID', wid);

  var r = String(paypalReturnUrl || '').trim();
  if (r) props.setProperty('PAYPAL_RETURN_URL', r);

  var c = String(paypalCancelUrl || '').trim();
  if (c) props.setProperty('PAYPAL_CANCEL_URL', c);

  return {
    ok: true,
    PAYPAL_ENV: props.getProperty('PAYPAL_ENV') || '',
    PAYPAL_CLIENT_ID: props.getProperty('PAYPAL_CLIENT_ID') ? '(set)' : '',
    PAYPAL_CLIENT_SECRET: props.getProperty('PAYPAL_CLIENT_SECRET') ? '(set)' : '',
    PAYPAL_WEBHOOK_ID: props.getProperty('PAYPAL_WEBHOOK_ID') || '',
    PAYPAL_RETURN_URL: props.getProperty('PAYPAL_RETURN_URL') || '',
    PAYPAL_CANCEL_URL: props.getProperty('PAYPAL_CANCEL_URL') || ''
  };
}

function evidexConfigurePayFast(payfastEnv, merchantId, merchantKey, passphrase, returnUrl, cancelUrl, notifyUrl, currency, amount) {
  var props = PropertiesService.getScriptProperties();

  var env = String(payfastEnv || '').trim();
  if (env) props.setProperty('PAYFAST_ENV', env);

  var mid = String(merchantId || '').trim();
  if (mid) props.setProperty('PAYFAST_MERCHANT_ID', mid);

  var mkey = String(merchantKey || '').trim();
  if (mkey) props.setProperty('PAYFAST_MERCHANT_KEY', mkey);

  var pp = String(passphrase || '').trim();
  if (pp) props.setProperty('PAYFAST_PASSPHRASE', pp);

  var r = String(returnUrl || '').trim();
  if (r) props.setProperty('PAYFAST_RETURN_URL', r);

  var c = String(cancelUrl || '').trim();
  if (c) props.setProperty('PAYFAST_CANCEL_URL', c);

  var n = String(notifyUrl || '').trim();
  if (n) props.setProperty('PAYFAST_NOTIFY_URL', n);

  var cur = String(currency || '').trim();
  if (cur) props.setProperty('PAYFAST_CURRENCY', cur);

  var amt = String(amount || '').trim();
  if (amt) props.setProperty('PAYFAST_AMOUNT', amt);

  return {
    ok: true,
    PAYFAST_ENV: props.getProperty('PAYFAST_ENV') || '',
    PAYFAST_MERCHANT_ID: props.getProperty('PAYFAST_MERCHANT_ID') || '',
    PAYFAST_MERCHANT_KEY: props.getProperty('PAYFAST_MERCHANT_KEY') ? '(set)' : '',
    PAYFAST_PASSPHRASE: props.getProperty('PAYFAST_PASSPHRASE') ? '(set)' : '',
    PAYFAST_RETURN_URL: props.getProperty('PAYFAST_RETURN_URL') || '',
    PAYFAST_CANCEL_URL: props.getProperty('PAYFAST_CANCEL_URL') || '',
    PAYFAST_NOTIFY_URL: props.getProperty('PAYFAST_NOTIFY_URL') || '',
    PAYFAST_CURRENCY: props.getProperty('PAYFAST_CURRENCY') || '',
    PAYFAST_AMOUNT: props.getProperty('PAYFAST_AMOUNT') || ''
  };
}

// Create and populate the standard EVIDEX intake form.
// Requires create_grant_reporting_form.gs to be present in the same project.
function evidexCreateIntakeForm() {
  if (typeof createFormWithRetry_ !== 'function' || typeof populateEvidexUniversalIntakeForm_ !== 'function') {
    throw new Error('Missing dependencies. Ensure create_grant_reporting_form.gs is in the project and pushed.');
  }

  var form = createFormWithRetry_('Intake - EVIDEX Evidence Pack (48h)', 6);
  populateEvidexUniversalIntakeForm_(form);

  var editUrl = form.getEditUrl();
  var viewUrl = '';
  try {
    viewUrl = form.getPublishedUrl();
  } catch (e) {
    // Some accounts restrict published URL access until settings are saved; ignore.
    viewUrl = '';
  }

  var formId = '';
  try {
    formId = extractFormIdFromUrl_(editUrl);
  } catch (e) {
    // Fallback best-effort extraction.
    var m = String(editUrl || '').match(/\/forms\/d\/(?:e\/)?([^\/\?]+)\//i);
    if (m && m[1]) formId = m[1];
  }

  if (formId) {
    PropertiesService.getScriptProperties().setProperty('FORM_ID', formId);
  }

  return {
    ok: true,
    formId: formId,
    editUrl: editUrl,
    viewUrl: viewUrl
  };
}

function evidexMirrorProbe(fileName) {
  var props = PropertiesService.getScriptProperties();
  var rootId = String(props.getProperty('EVIDENCE_ENGINE_ROOT_FOLDER_ID') || '').trim();
  if (!rootId) throw new Error('Missing Script Property: EVIDENCE_ENGINE_ROOT_FOLDER_ID');

  var name = String(fileName || '').trim();
  if (!name) throw new Error('fileName is required');

  var root = DriveApp.getFolderById(rootId);
  var it = root.getFilesByName(name);
  var found = it.hasNext();
  var fileId = '';
  if (found) {
    try {
      fileId = it.next().getId();
    } catch (e) {
      fileId = '';
    }
  }

  return {
    ok: true,
    found: found,
    fileName: name,
    fileId: fileId,
    checkedFolderId: rootId
  };
}

function evidexMirrorProbeCleanup(fileName) {
  var props = PropertiesService.getScriptProperties();
  var rootId = String(props.getProperty('EVIDENCE_ENGINE_ROOT_FOLDER_ID') || '').trim();
  if (!rootId) throw new Error('Missing Script Property: EVIDENCE_ENGINE_ROOT_FOLDER_ID');

  var name = String(fileName || '').trim();
  if (!name) throw new Error('fileName is required');

  var root = DriveApp.getFolderById(rootId);
  var it = root.getFilesByName(name);
  var removed = 0;
  while (it.hasNext()) {
    var f = it.next();
    try {
      f.setTrashed(true);
      removed++;
    } catch (e) {
      // ignore
    }
  }

  return { ok: true, removed: removed, fileName: name };
}

// --- Test utilities ---

function _evidexGetFormId_() {
  var props = PropertiesService.getScriptProperties();
  return String(props.getProperty('FORM_ID') || (typeof FORM_ID !== 'undefined' ? FORM_ID : '') || '').trim();
}

function _evidexGetRootFolderId_() {
  var props = PropertiesService.getScriptProperties();
  return String(props.getProperty('EVIDENCE_ENGINE_ROOT_FOLDER_ID') || (typeof EVIDENCE_ENGINE_ROOT_FOLDER_ID !== 'undefined' ? EVIDENCE_ENGINE_ROOT_FOLDER_ID : '') || '').trim();
}

function _evidexNorm_(s) {
  if (typeof normTitle_ === 'function') return normTitle_(s);
  return String(s || '')
    .toLowerCase()
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .replace(/\s+/g, ' ');
}

function evidexCreateTestEvidenceFolder() {
  var rootId = _evidexGetRootFolderId_();
  if (!rootId) throw new Error('Missing Script Property: EVIDENCE_ENGINE_ROOT_FOLDER_ID');

  var root = DriveApp.getFolderById(rootId);
  var folder = root.createFolder('TEST_EVIDENCE__' + new Date().toISOString().replace(/[:\.]/g, '-'));
  folder.createFile('README_TEST_EVIDENCE.txt', 'Test evidence generated by Apps Script.\n', MimeType.PLAIN_TEXT);
  folder.createFile('kpi_notes.txt', 'KPI Notes:\n- Beneficiaries served: 123\n- Clinics supported: 4\n', MimeType.PLAIN_TEXT);

  return {
    ok: true,
    folderId: folder.getId(),
    folderUrl: folder.getUrl(),
    name: folder.getName()
  };
}

function evidexInspectForm() {
  var formId = _evidexGetFormId_();
  if (!formId) throw new Error('Missing Script Property: FORM_ID');

  var form = FormApp.openById(formId);
  var items = form.getItems();
  var out = [];
  for (var i = 0; i < items.length; i++) {
    var it = items[i];
    var title = '';
    var required = false;
    var type = '';
    try { title = String(it.getTitle() || ''); } catch (e) { title = ''; }
    try { required = !!it.isRequired(); } catch (e) { required = false; }
    try { type = String(it.getType()); } catch (e) { type = ''; }
    out.push({ index: i, title: title, required: required, type: type });
  }
  return { ok: true, formId: formId, itemCount: items.length, items: out };
}

// Submit a real test response into the configured Google Form.
// This fires installable onSubmit triggers just like a user submission.
// Note: file uploads cannot be submitted programmatically; use the "Upload folder link" fallback.
function evidexSubmitTestResponse(overridesJson) {
  var formId = _evidexGetFormId_();
  if (!formId) throw new Error('Missing Script Property: FORM_ID');

  var overrides = {};
  if (overridesJson) {
    try {
      overrides = JSON.parse(String(overridesJson));
    } catch (e) {
      throw new Error('overridesJson must be valid JSON string');
    }
  }

  var allowRelaxUnsupported = !!(overrides && (overrides.allowRelaxUnsupported === true || overrides.allowRelaxUnsupported === 1 || overrides.allowRelaxUnsupported === '1'));

  // Create a small Drive folder with sample evidence and point the intake at it.
  var evidence = null;
  try {
    evidence = evidexCreateTestEvidenceFolder();
  } catch (e) {
    evidence = null;
  }

  var defaults = {
    'Organization': 'EVIDEX Test Org',
    'Project / engagement name': 'EVIDEX Test Pack',
    'Reporting period start date': '2025-10-01',
    'Reporting period end date': '2025-12-31',
    'Your name': 'Test Submitter',
    'Email for delivery': 'evidex.ops@gmail.com',
    'Pack purpose': 'Automated test submission',
    'Donor / funder name': 'Test Donor',
    'Tone': 'neutral',
    'Include appendix summaries?': 'No',
    'Upload folder link (Drive/OneDrive)': evidence && evidence.folderUrl ? evidence.folderUrl : ''
  };

  // Merge overrides.
  for (var k in overrides) {
    if (Object.prototype.hasOwnProperty.call(overrides, k)) {
      defaults[k] = overrides[k];
    }
  }

  var form = FormApp.openById(formId);
  var resp = form.createResponse();
  var items = form.getItems();

  // If the form has required items we can't satisfy programmatically (most notably FILE_UPLOAD), fail fast
  // with a clear message (or optionally relax required flags for test runs).
  var unsupportedRequired = [];
  for (var ri = 0; ri < items.length; ri++) {
    var it0 = items[ri];
    var req0 = false;
    try { req0 = !!it0.isRequired(); } catch (e) { req0 = false; }
    if (!req0) continue;
    var t0 = '';
    try { t0 = String(it0.getTitle() || ''); } catch (e) { t0 = ''; }
    var ty0 = '';
    try { ty0 = String(it0.getType()); } catch (e) { ty0 = ''; }

    // Types we do NOT currently support for programmatic answering.
    if (it0.getType && (it0.getType() === FormApp.ItemType.FILE_UPLOAD || it0.getType() === FormApp.ItemType.GRID || it0.getType() === FormApp.ItemType.CHECKBOX_GRID || it0.getType() === FormApp.ItemType.TIME || it0.getType() === FormApp.ItemType.DATETIME)) {
      unsupportedRequired.push({ title: t0, type: ty0 });
      if (allowRelaxUnsupported) {
        try { it0.setRequired(false); } catch (e) {}
      }
    }
  }
  if (unsupportedRequired.length && !allowRelaxUnsupported) {
    throw new Error(
      'Form has required item types that cannot be test-submitted programmatically (likely File Upload). ' +
        'Either run a manual submission, or re-run evidexSubmitTestResponse with overridesJson {"allowRelaxUnsupported":true}. ' +
        'Unsupported required: ' + JSON.stringify(unsupportedRequired)
    );
  }

  function _parseDate_(s) {
    var str = String(s || '').trim();
    if (!str) return null;
    var m = str.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    var d = new Date(str);
    if (!isNaN(d.getTime())) return d;
    return null;
  }

  function _firstChoiceValue_(choices) {
    try {
      if (choices && choices.length) return String(choices[0].getValue() || '');
    } catch (e) {}
    return '';
  }

  var setCount = 0;
  var skipped = [];
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var title = '';
    try {
      title = String(item.getTitle() || '');
    } catch (e) {
      title = '';
    }

    var required = false;
    try {
      required = !!item.isRequired();
    } catch (e) {
      required = false;
    }

    var value = null;
    // Find a matching default key by normalized/partial title.
    var tNorm = _evidexNorm_(title);
    for (var dk in defaults) {
      if (!Object.prototype.hasOwnProperty.call(defaults, dk)) continue;
      var dNorm = _evidexNorm_(dk);
      if (!dNorm) continue;
      if (tNorm === dNorm || tNorm.indexOf(dNorm) >= 0 || dNorm.indexOf(tNorm) >= 0) {
        value = defaults[dk];
        break;
      }
    }
    if (value == null && !required) continue;

    // If required and unmapped, supply safe defaults by type.
    if (value == null && required) {
      value = '';
    }

    try {
      var type = item.getType();
      if (type === FormApp.ItemType.TEXT) {
        resp.withItemResponse(item.asTextItem().createResponse(String(value || '').trim() || (required ? 'N/A' : '')));
        setCount++;
      } else if (type === FormApp.ItemType.PARAGRAPH_TEXT) {
        resp.withItemResponse(item.asParagraphTextItem().createResponse(String(value || '').trim() || (required ? 'N/A' : '')));
        setCount++;
      } else if (type === FormApp.ItemType.DATE) {
        var d = _parseDate_(value);
        if (!d && required) d = new Date();
        if (d) {
          resp.withItemResponse(item.asDateItem().createResponse(d));
          setCount++;
        } else {
          skipped.push({ title: title, reason: 'missing date value', value: value });
        }
      } else if (type === FormApp.ItemType.MULTIPLE_CHOICE) {
        var mc = item.asMultipleChoiceItem();
        var s = String(value || '').trim();
        // Only set if choice exists.
        var choices = mc.getChoices();
        var ok = false;
        for (var c = 0; c < choices.length; c++) {
          if (String(choices[c].getValue() || '') === s) {
            ok = true;
            break;
          }
        }
        if (!ok && required) {
          s = _firstChoiceValue_(choices);
          ok = !!s;
        }
        if (ok) {
          resp.withItemResponse(mc.createResponse(s));
          setCount++;
        } else {
          skipped.push({ title: title, reason: 'choice not found', value: s });
        }
      } else if (type === FormApp.ItemType.LIST) {
        var li = item.asListItem();
        var s2 = String(value || '').trim();
        var choices2 = li.getChoices();
        var ok2 = false;
        for (var j = 0; j < choices2.length; j++) {
          if (String(choices2[j].getValue() || '') === s2) {
            ok2 = true;
            break;
          }
        }
        if (!ok2 && required) {
          s2 = _firstChoiceValue_(choices2);
          ok2 = !!s2;
        }
        if (ok2) {
          resp.withItemResponse(li.createResponse(s2));
          setCount++;
        } else {
          skipped.push({ title: title, reason: 'list choice not found', value: s2 });
        }
      } else if (type === FormApp.ItemType.CHECKBOX) {
        var cb = item.asCheckboxItem();
        var raw = value;
        var arr = [];
        if (Array.isArray(raw)) {
          for (var a = 0; a < raw.length; a++) arr.push(String(raw[a] || '').trim());
        } else {
          var s3 = String(raw || '').trim();
          if (s3) arr = s3.split(/\s*,\s*|\n+/g).filter(function(x){ return String(x||'').trim(); });
        }
        var cbChoices = cb.getChoices();
        if ((!arr || !arr.length) && required) {
          var first = _firstChoiceValue_(cbChoices);
          if (first) arr = [first];
        }
        if (arr && arr.length) {
          resp.withItemResponse(cb.createResponse(arr));
          setCount++;
        } else {
          skipped.push({ title: title, reason: 'checkbox empty', value: value });
        }
      } else if (type === FormApp.ItemType.SCALE) {
        var sc = item.asScaleItem();
        var n = parseInt(String(value || ''), 10);
        if (isNaN(n)) {
          try { n = sc.getLowerBound(); } catch (e) { n = 1; }
        }
        resp.withItemResponse(sc.createResponse(n));
        setCount++;
      } else {
        skipped.push({ title: title, reason: 'unsupported item type', type: String(type), value: value });
      }
    } catch (e) {
      skipped.push({ title: title, reason: 'exception', error: String(e) });
    }
  }

  var submitted = null;
  try {
    submitted = resp.submit();
  } catch (e) {
    throw new Error('Test submission failed. Likely missing a required answer for a non-text item. Error: ' + e);
  }
  return {
    ok: true,
    formId: formId,
    responseId: submitted.getId ? submitted.getId() : '',
    editResponseUrl: submitted.getEditResponseUrl ? submitted.getEditResponseUrl() : '',
    setCount: setCount,
    skipped: skipped,
    evidenceFolder: evidence
  };
}
