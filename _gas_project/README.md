# Create the Google Form (Grant Reporting Intake)

You *can’t* programmatically create a Google Form from this repo without running code inside **your** Google account.
This folder gives you a 2-minute way to do it via Google Apps Script.

The generated Form now includes:
- broader pack purposes (audit/tax/ESG/university grant closeout), and
- an optional **Billing** section (`Client type`, `Order size (packs)`, `Currency`) that can drive tiered pricing.

## Steps
1) Open https://script.google.com and create a **New project**.
2) Copy/paste the contents of `create_grant_reporting_form.gs` into `Code.gs`.
3) Click **Run** and choose the function `createGrantReportingIntakeForm`.
4) Approve permissions when prompted.
5) Open **Executions** or **Logs** to get:
   - Edit URL (you can edit the form)
   - Public URL (you can send to clients)

## If you get: “Failed to create new form. Please wait and try again.”
Some Google Workspace setups block scripted creation of new Forms (even if manual creation works).

Use this fallback:
1) Create a blank Form manually at https://forms.google.com
2) Copy the Form ID from the URL: `https://docs.google.com/forms/d/<FORM_ID>/edit`
3) In Apps Script, run: `populateExistingGrantReportingForm('<FORM_ID>')`

This populates your existing Form with all the questions.

## Tip (best practice)
In the form settings:
- Turn on **Collect email addresses** (if you want stricter delivery tracking).
- Add a short “what to upload” instruction and required file naming convention.
