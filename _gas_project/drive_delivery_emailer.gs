/**
 * Drive Deliveries Emailer (Google Apps Script)
 *
 * Purpose: fully automate delivery email once the local watcher syncs DELIVERABLE.zip back into Drive.
 *
 * How it works:
 * - Runs on a time-based trigger (every few minutes).
 * - Scans EvidenceEngine/done/<job>/ for:
 *    - DELIVERABLE.zip
 *    - CONTACT_EMAIL.txt
 *    - (optional) PAID.txt  (recommended if you require payment)
 * - If deliverable exists and SENT.txt doesn't, sends an email with links and marks SENT.txt.
 *
 * Required Script Properties:
 * - EVIDENCE_ENGINE_ROOT_FOLDER_ID (Drive folder id for EvidenceEngine)
 *
 * Optional Script Properties:
 * - REQUIRE_PAYMENT (1/0) default 0
 * - DELIVERY_FROM_NAME (display name)
 */

// Optional fallback if you don't want to use Script Properties.
// (EVIDEX UI/clasp can inject this automatically.)
var EVIDENCE_ENGINE_ROOT_FOLDER_ID = '1idb7x7NKJF2YrS_DXEqYvkIFMa6JfWa5';

function _ddGetProp_(name) {
  var v = PropertiesService.getScriptProperties().getProperty(name);
  return String(v || '').trim();
}

function _ddTruthy_(v) {
  var s = String(v || '').trim().toLowerCase();
  return (s === '1' || s === 'true' || s === 'yes' || s === 'y' || s === 'on');
}

function _ddRequireProp_(name) {
  var v = _ddGetProp_(name);
  if (!v && name === 'EVIDENCE_ENGINE_ROOT_FOLDER_ID') {
    v = String(EVIDENCE_ENGINE_ROOT_FOLDER_ID || '').trim();
  }
  if (!v) throw new Error('Missing Script Property: ' + name);
  return v;
}

function setupDeliveryTrigger() {
  // Remove existing triggers for this handler to avoid duplicates.
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    var t = triggers[i];
    try {
      if (t.getHandlerFunction && t.getHandlerFunction() === 'scanAndSendDeliveries') {
        ScriptApp.deleteTrigger(t);
      }
    } catch (e) {
      // ignore
    }
  }
  // Runs every 5 minutes.
  ScriptApp.newTrigger('scanAndSendDeliveries')
    .timeBased()
    .everyMinutes(5)
    .create();
}

function scanAndSendDeliveries() {
  var rootId = _ddRequireProp_('EVIDENCE_ENGINE_ROOT_FOLDER_ID');
  var requirePayment = _ddTruthy_(_ddGetProp_('REQUIRE_PAYMENT') || '0');

  var root = DriveApp.getFolderById(rootId);
  var done = _findFolderByName_(root, 'done');
  try {
    _upsertTextFile_(root, '_delivery_emailer_last_run.txt', 'Last run: ' + new Date().toISOString() + '\n');
  } catch (e) {
    // ignore heartbeat failures
  }

  if (!done) {
    try {
      _upsertTextFile_(root, '_delivery_emailer_last_error.txt', 'Missing done/ folder at ' + new Date().toISOString() + '\n');
    } catch (e2) {
      // ignore
    }
    return;
  }

  var jobs = done.getFolders();
  while (jobs.hasNext()) {
    var jobFolder = jobs.next();
    try {
      _processJobFolder_(jobFolder, requirePayment);
    } catch (e) {
      Logger.log('Delivery scan error for ' + jobFolder.getName() + ': ' + e);
      try {
        _upsertTextFile_(jobFolder, 'DELIVERY_ERROR.txt', 'Delivery error at ' + new Date().toISOString() + '\n' + String(e) + '\n');
      } catch (e2) {
        // ignore
      }
    }
  }
}

function _processJobFolder_(jobFolder, requirePayment) {
  if (_fileExists_(jobFolder, 'SENT.txt')) {
    _upsertTextFile_(jobFolder, 'DELIVERY_STATUS.txt', 'Status: sent\n');
    return;
  }

  if (requirePayment && !_fileExists_(jobFolder, 'PAID.txt')) {
    _upsertTextFile_(jobFolder, 'DELIVERY_STATUS.txt', 'Status: blocked (waiting payment)\n');
    return;
  }

  var contactEmail = _readSmallFile_(jobFolder, 'CONTACT_EMAIL.txt');
  if (!contactEmail) {
    _upsertTextFile_(jobFolder, 'DELIVERY_STATUS.txt', 'Status: blocked (missing CONTACT_EMAIL.txt)\n');
    return;
  }

  var deliverable = _getFileByName_(jobFolder, 'DELIVERABLE.zip');
  if (!deliverable) {
    _upsertTextFile_(jobFolder, 'DELIVERY_STATUS.txt', 'Status: blocked (missing DELIVERABLE.zip)\n');
    return;
  }

  var jobName = jobFolder.getName();
  var deliverableUrl = deliverable.getUrl();
  var jobUrl = jobFolder.getUrl();

  var subject = 'EVIDEX: Your Evidence Pack is ready (' + jobName + ')';
  var body =
    'EVIDEX: your Evidence Pack is ready.\n\n' +
    'Download ZIP:\n' + deliverableUrl + '\n\n' +
    'Job folder (audit trail):\n' + jobUrl + '\n\n' +
    'If you have any questions, reply to this email.';

  MailApp.sendEmail({
    to: contactEmail,
    subject: subject,
    body: body
  });

  jobFolder.createFile('SENT.txt', 'Sent at ' + new Date().toISOString() + '\n', MimeType.PLAIN_TEXT);
  _upsertTextFile_(jobFolder, 'DELIVERY_STATUS.txt', 'Status: sent\n');
}

function _upsertTextFile_(folder, name, content) {
  var it = folder.getFilesByName(name);
  if (it.hasNext()) {
    var f = it.next();
    try {
      f.setContent(String(content || ''));
      return;
    } catch (e) {
      // fall through
    }
  }
  folder.createFile(name, String(content || ''), MimeType.PLAIN_TEXT);
}

function _findFolderByName_(parent, name) {
  var it = parent.getFoldersByName(name);
  return it.hasNext() ? it.next() : null;
}

function _fileExists_(folder, name) {
  var it = folder.getFilesByName(name);
  return it.hasNext();
}

function _getFileByName_(folder, name) {
  var it = folder.getFilesByName(name);
  return it.hasNext() ? it.next() : null;
}

function _readSmallFile_(folder, name) {
  var f = _getFileByName_(folder, name);
  if (!f) return '';
  try {
    return String(f.getBlob().getDataAsString() || '').trim();
  } catch (e) {
    return '';
  }
}
