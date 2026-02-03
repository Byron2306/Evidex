/**
 * Google Apps Script: creates the "Grant Reporting Evidence Pack" intake Google Form.
 *
 * How to run:
 * 1) Go to script.google.com -> New project
 * 2) Paste this file contents into Code.gs
 * 3) Run createGrantReportingIntakeForm()
 * 4) Approve permissions
 * 5) The Form URL will be logged.
 *
 * Note:
 * Google sometimes returns a transient error: "Failed to create new form. Please wait and try again."
 * This script retries with backoff to reduce failures.
 */

// Keep in sync with scripts/google_forms/form_submit_to_drive_jobs.gs
var FILE_UPLOAD_QUESTION_TITLE = 'Upload files (PDF/DOCX/XLSX)';
var UPLOADS_SECTION_TITLE = 'E) Uploads';

// Optional: place a logo at the top of the Form.
// Set a Drive file id in Script Properties:
//   EVIDEX_LOGO_FILE_ID = <png/jpg file id>
// Tip: upload this repo's logo file to Drive first:
//   assets/evidex_logo.png
var EVIDEX_LOGO_PROP = 'EVIDEX_LOGO_FILE_ID';

function _grGetProp_(key) {
  return PropertiesService.getScriptProperties().getProperty(String(key));
}

function createFormWithRetry_(title, maxAttempts) {
  var attempts = maxAttempts || 6;
  var lastErr = null;
  for (var i = 1; i <= attempts; i++) {
    try {
      return FormApp.create(title);
    } catch (e) {
      lastErr = e;
      Logger.log('Form create attempt ' + i + ' failed: ' + e);
      // Exponential backoff: 1s, 2s, 4s, 8s, 16s, ... (cap ~30s)
      var sleepMs = Math.min(30000, Math.pow(2, i - 1) * 1000);
      Utilities.sleep(sleepMs);
    }
  }
  throw lastErr;
}

function extractFormIdFromUrl_(formUrl) {
  if (!formUrl) {
    throw new Error('formUrl is required');
  }
  // Supports URLs like:
  // - https://docs.google.com/forms/d/e/<FORM_ID>/viewform
  // - https://docs.google.com/forms/d/<FORM_ID>/edit
  var m = String(formUrl).match(/\/forms\/d\/(?:e\/)?([^\/\?]+)\//i);
  if (!m || !m[1]) {
    throw new Error('Could not extract form ID from URL. Paste a URL containing /forms/d/...');
  }
  return m[1];
}

function _tryAddEvidexLogo_(form) {
  try {
    var fileId = _grGetProp_(EVIDEX_LOGO_PROP) || '';
    if (!fileId) return;
    var blob = DriveApp.getFileById(String(fileId)).getBlob();
    form.addImageItem().setImage(blob);
  } catch (e) {
    Logger.log('Could not add logo image: ' + e);
  }
}

function populateEvidexUniversalIntakeForm_(form) {
  form.setCollectEmail(true);
  form.setTitle('EVIDEX Intake — Evidence & Compliance Pack (48h)');
  form.setDescription(
    'EVIDEX turns your existing documents into an audit-ready evidence pack within 48 hours.\n' +
    'Confidential / white-label by default. Human QA + automation.\n\n' +
    'Start by telling us your capacity/field so we only ask what’s relevant.'
  );

  _tryAddEvidexLogo_(form);

  // Section A — Contact + delivery
  form.addPageBreakItem().setTitle('A) Contact + delivery');
  form.addTextItem().setTitle('Your name').setRequired(true);
  form.addTextItem().setTitle('Organization').setRequired(true);
  form.addMultipleChoiceItem()
    .setTitle('Role')
    .setChoiceValues(['Program Manager', 'M&E', 'Finance', 'Operations', 'Consultant', 'Other'])
    .setRequired(true);
  form.addTextItem().setTitle('Email for delivery').setHelpText('If different from the submitter email.').setRequired(true);
  form.addTextItem().setTitle('Time zone').setHelpText('e.g., UTC+1, EST, GMT').setRequired(true);
  form.addTextItem()
    .setTitle('Deadline date/time')
    .setHelpText('e.g., 2026-01-24 17:00 local time')
    .setRequired(true);
  form.addMultipleChoiceItem()
    .setTitle('Confidentiality confirmation')
    .setHelpText('Confirm you have the right to share the uploaded materials for analysis and pack generation.')
    .setChoiceValues(['Yes', 'No'])
    .setRequired(true);

  // Section B — Capacity/field (branching)
  form.addPageBreakItem().setTitle('B) Your capacity / field');

  // Create branch sections first so we can route to them.
  var secGrant = form.addPageBreakItem().setTitle('B1) Grant-funded / NGO');
  form.addTextItem().setTitle('Grant / project name').setHelpText('The name used for reporting (or short project label).').setRequired(true);
  form.addTextItem().setTitle('Donor / funder name').setRequired(false);
  form.addTextItem().setTitle('Grant ID (optional)').setRequired(false);

  var secCorporate = form.addPageBreakItem().setTitle('B2) Corporate / compliance / ESG');
  form.addTextItem().setTitle('Project / engagement name').setHelpText('Internal project name, audit name, ESG report cycle, etc.').setRequired(true);
  form.addTextItem().setTitle('Stakeholder / auditor / regulator name').setRequired(false);
  form.addMultipleChoiceItem()
    .setTitle('Pack purpose')
    .setChoiceValues([
      'Annual audit support',
      'Tax/VAT compliance evidence',
      'Internal controls / compliance',
      'ESG/CSI reporting support',
      'Board update'
    ])
    .setRequired(true);

  var secUni = form.addPageBreakItem().setTitle('B3) University / research / sponsor compliance');
  form.addTextItem().setTitle('Project / engagement name').setHelpText('Award title, study title, or internal name.').setRequired(true);
  form.addTextItem().setTitle('Sponsor / funder name').setRequired(false);
  form.addTextItem().setTitle('Award / grant number (optional)').setRequired(false);
  form.addMultipleChoiceItem()
    .setTitle('Pack purpose')
    .setChoiceValues([
      'University grant closeout / research compliance',
      'Donor funding report',
      'Board update'
    ])
    .setRequired(true);

  var secConsult = form.addPageBreakItem().setTitle('B4) Consultancy / agency (white-label)');
  form.addTextItem().setTitle('Client organization (optional)').setRequired(false);
  form.addTextItem().setTitle('Project / engagement name').setHelpText('Your internal case/project name.').setRequired(true);
  form.addMultipleChoiceItem()
    .setTitle('Client type')
    .setChoiceValues(['NGO/grant-funded', 'Corporate', 'Consultancy'])
    .setRequired(false);
  form.addMultipleChoiceItem()
    .setTitle('Order size (packs)')
    .setChoiceValues(['1', '5', '10'])
    .setHelpText('Consultancies often buy 5/10 packs and resell internally.')
    .setRequired(false);
  form.addMultipleChoiceItem()
    .setTitle('Currency')
    .setChoiceValues(['USD', 'ZAR', 'Other'])
    .setRequired(false);

  var secOther = form.addPageBreakItem().setTitle('B5) Other / not sure');
  form.addTextItem().setTitle('Project / engagement name').setHelpText('Give this a short name for reference.').setRequired(true);
  form.addParagraphTextItem().setTitle('Describe your use case').setHelpText('What do you need the evidence pack for, and who is it for?').setRequired(true);
  form.addMultipleChoiceItem()
    .setTitle('Pack purpose')
    .setChoiceValues([
      'Donor funding report',
      'Annual audit support',
      'Tax/VAT compliance evidence',
      'Internal controls / compliance',
      'ESG/CSI reporting support',
      'University grant closeout / research compliance',
      'Board update'
    ])
    .setRequired(true);

  // Common section C — Reporting window
  var secWindow = form.addPageBreakItem().setTitle('C) Reporting window');
  form.addTextItem().setTitle('Reporting period start date').setHelpText('YYYY-MM-DD').setRequired(true);
  form.addTextItem().setTitle('Reporting period end date').setHelpText('YYYY-MM-DD').setRequired(true);

  // Section D — KPIs
  form.addPageBreakItem().setTitle('D) KPIs (evidence table)');
  form.addMultipleChoiceItem()
    .setTitle('How will you provide KPIs?')
    .setChoiceValues(['Upload KPI spreadsheet (preferred)', 'Paste KPIs', 'No KPIs (documents only)'])
    .setRequired(true);

  form.addParagraphTextItem()
    .setTitle('If pasting KPIs: list KPIs (one per line)')
    .setHelpText('Format suggestion: KPI name | target | actual | how measured')
    .setRequired(false);

  // Section E — Narrative requirements
  form.addPageBreakItem().setTitle('E) Narrative requirements');
  form.addMultipleChoiceItem()
    .setTitle('Tone')
    .setChoiceValues(['formal', 'neutral', 'donor-friendly'])
    .setRequired(true);
  form.addParagraphTextItem()
    .setTitle('Required headings (if a template exists)')
    .setHelpText('Paste required headings / sections, or note "use provided template".')
    .setRequired(false);
  form.addParagraphTextItem()
    .setTitle('Risk sensitivities (anything we must avoid stating)')
    .setHelpText('E.g., do not claim outcomes not directly measured.')
    .setRequired(false);
  form.addParagraphTextItem()
    .setTitle('Red flags / known gaps (missing docs, data issues)')
    .setRequired(false);

  // Section F — Uploads
  form.addPageBreakItem().setTitle(UPLOADS_SECTION_TITLE);

  // Recommended: File upload question (best automation).
  // Note: Only available for some Google Workspace accounts.
  if (!hasItemWithTitle_(form, FILE_UPLOAD_QUESTION_TITLE)) {
    try {
      form.addFileUploadItem()
        .setTitle(FILE_UPLOAD_QUESTION_TITLE)
        .setHelpText('Upload PDFs, DOCX, XLSX, CSV. If you can’t upload here, use the folder link below.')
        .setRequired(false);
    } catch (e) {
      Logger.log('Could not add File upload item (account policy may block it): ' + e);
    }
  }

  form.addTextItem()
    .setTitle('Upload folder link (Drive/OneDrive)')
    .setHelpText('If you can’t upload directly, create a Google Drive folder, share it with evidex.ops@gmail.com (viewer is fine), then paste the folder link here. If sensitive, you can share a time-limited link.')
    .setRequired(false);
  form.addCheckboxItem()
    .setTitle('What are you providing?')
    .setChoiceValues([
      'KPI sheet / logframe',
      'Activity report(s)',
      'Financial summary',
      'Receipts/invoices list',
      'Meeting minutes',
      'Monitoring visit notes',
      'Key email threads (optional)'
    ])
    .setRequired(true);

  // Section G — Output preferences
  form.addPageBreakItem().setTitle('G) Output preferences');
  form.addMultipleChoiceItem()
    .setTitle('File format preference')
    .setChoiceValues(['DOCX + XLSX (default)', 'PDF (optional)'])
    .setRequired(true);
  form.addMultipleChoiceItem()
    .setTitle('Include appendix summaries?')
    .setChoiceValues(['Yes', 'No'])
    .setRequired(true);
  form.addTextItem()
    .setTitle('White-label naming (optional)')
    .setHelpText('Brand name to appear on the cover, if any.')
    .setRequired(false);

  // Now create the initial branching question that routes into the correct section.
  // Place it *after* the section exists, then move it to the top of section B.
  var capItem = form.addMultipleChoiceItem().setTitle('In which capacity or field are you?').setRequired(true);
  capItem.setChoices([
    capItem.createChoice('Grant-funded NGO / NPO / donor reporting', secGrant),
    capItem.createChoice('Corporate (audit / compliance / ESG)', secCorporate),
    capItem.createChoice('University / research sponsor compliance', secUni),
    capItem.createChoice('Consultancy / agency (white-label)', secConsult),
    capItem.createChoice('Other / not sure', secOther)
  ]);

  // Try to relocate the branching question directly under section B page break.
  try {
    var items = form.getItems();
    var bIndex = -1;
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      if (it.getType && it.getType() === FormApp.ItemType.PAGE_BREAK && it.getTitle && it.getTitle() === 'B) Your capacity / field') {
        bIndex = i;
        break;
      }
    }
    if (bIndex >= 0) {
      // Use the most compatible signature: moveItem(fromIndex, toIndex)
      // Some runtimes do not support moveItem(Item, number).
      if (capItem && typeof capItem.getIndex === 'function') {
        form.moveItem(capItem.getIndex(), bIndex + 1);
      }
    }
  } catch (eMove) {
    Logger.log('Could not move branching item into section B: ' + eMove);
  }

  // Ensure each branch section jumps to the common reporting window section.
  // Some runtimes support setGoToPage(PageBreakItem); if not, users will still be able to submit
  // but may have to click through extra sections.
  try { secGrant.setGoToPage(secWindow); } catch (e1) {}
  try { secCorporate.setGoToPage(secWindow); } catch (e2) {}
  try { secUni.setGoToPage(secWindow); } catch (e3) {}
  try { secConsult.setGoToPage(secWindow); } catch (e4) {}
  try { secOther.setGoToPage(secWindow); } catch (e5) {}

  // Confirmation
  form.setConfirmationMessage('Thanks — EVIDEX will confirm receipt and deliver your evidence pack within 48 hours.');

  Logger.log('Form created: ' + form.getEditUrl());
  Logger.log('Public URL: ' + form.getPublishedUrl());
}

// Backwards-compatible name (older docs call this)
function populateGrantReportingIntakeForm_(form) {
  populateEvidexUniversalIntakeForm_(form);
}

function hasItemWithTitle_(form, title) {
  var items = form.getItems();
  for (var i = 0; i < items.length; i++) {
    var it = items[i];
    try {
      if (it && it.getTitle && it.getTitle() === title) return true;
    } catch (e) {
      // Ignore items that don't support getTitle.
    }
  }
  return false;
}

/**
 * Safe updater: adds the File Upload question to an existing form (without duplicating everything).
 *
 * Run this if your form already exists and you only want to enable uploads.
 */
function addFileUploadQuestionToExistingForm(formId) {
  if (!formId) throw new Error('formId is required');
  var form = FormApp.openById(formId);

  // File upload items are only available for some Google Workspace accounts.
  if (!form.addFileUploadItem) {
    Logger.log('This account does not support file upload questions (form.addFileUploadItem is unavailable).');
    Logger.log('Edit URL: ' + form.getEditUrl());
    return;
  }

  if (hasItemWithTitle_(form, FILE_UPLOAD_QUESTION_TITLE)) {
    Logger.log('File upload question already exists: ' + FILE_UPLOAD_QUESTION_TITLE);
    Logger.log('Edit URL: ' + form.getEditUrl());
    return;
  }

  var fileItem;
  try {
    fileItem = form.addFileUploadItem()
      .setTitle(FILE_UPLOAD_QUESTION_TITLE)
      .setHelpText('Upload PDFs, DOCX, XLSX, CSV. If you can’t upload here, use the folder link question.')
      .setRequired(false);
  } catch (e) {
    throw new Error('Could not add File upload item (Workspace policy may block it): ' + e);
  }

  // Try to move it into the "E) Uploads" section, right after that page break.
  try {
    var items = form.getItems();
    var uploadsBreakIndex = -1;
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      if (it.getType && it.getType() === FormApp.ItemType.PAGE_BREAK && it.getTitle && it.getTitle() === UPLOADS_SECTION_TITLE) {
        uploadsBreakIndex = i;
        break;
      }
    }
    if (uploadsBreakIndex >= 0) {
      if (fileItem && typeof fileItem.getIndex === 'function') {
        form.moveItem(fileItem.getIndex(), uploadsBreakIndex + 1);
      }
    }
  } catch (eMove) {
    Logger.log('Could not move file upload item into uploads section: ' + eMove);
  }

  Logger.log('Added file upload question. Edit URL: ' + form.getEditUrl());
}

function addFileUploadQuestionToExistingFormFromUrl(formUrl) {
  var formId = extractFormIdFromUrl_(formUrl);
  addFileUploadQuestionToExistingForm(formId);
}

/**
 * Preferred path: create a new Form, then populate it.
 * If your account blocks scripted form creation, use populateExistingGrantReportingForm(formId).
 */
function createGrantReportingIntakeForm() {
  // Some accounts fail with special characters in titles; keep creation title ASCII.
  var form = createFormWithRetry_('Intake - EVIDEX Evidence Pack (48h)', 6);
  populateEvidexUniversalIntakeForm_(form);
}

/**
 * Fallback: manually create a blank Form in Google Drive, then run this.
 *
 * Steps:
 * 1) Go to forms.google.com -> Blank form (create it manually)
 * 2) Copy the form ID from the URL: https://docs.google.com/forms/d/<FORM_ID>/edit
 * 3) Run populateExistingGrantReportingForm('<FORM_ID>')
 */
function populateExistingGrantReportingForm(formId) {
  if (!formId) {
    throw new Error('formId is required');
  }
  var form = FormApp.openById(formId);
  populateEvidexUniversalIntakeForm_(form);
  Logger.log('Populated existing form: ' + form.getEditUrl());
  Logger.log('Public URL: ' + form.getPublishedUrl());
}

/**
 * Convenience wrapper: paste the full Form URL and let the script extract the ID.
 */
function populateExistingGrantReportingFormFromUrl(formUrl) {
  var formId = extractFormIdFromUrl_(formUrl);
  populateExistingGrantReportingForm(formId);
}

/**
 * Your form URL (edit or view). Update this if you want one-click runs.
 */
var DEFAULT_FORM_URL = 'https://docs.google.com/forms/d/1p0V2osxfFIWWqqedalwBTIdIQEkqpdt1EDiV9VugeLk/edit#settings';

/**
 * One-click: populates DEFAULT_FORM_URL.
 */
function populateMyGrantReportingForm() {
  populateExistingGrantReportingFormFromUrl(DEFAULT_FORM_URL);
}

// Convenience runner so you can execute from the Apps Script UI dropdown.
// You can delete this after it succeeds.
function runAddUpload() {
  addFileUploadQuestionToExistingForm('1p0V2osxfFIWWqqedalwBTIdIQEkqpdt1EDiV9VugeLk');
}
