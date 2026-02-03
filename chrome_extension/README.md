# Chrome extension: Daily Outreach Helper

This is a **compliance-first** outreach helper:
- Schedules **1 reminder per day**
- Opens posting pages with **prefilled** text
- You still click **Post** (avoids brittle automation + account risk)

## Install locally
1. Open Chrome → `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked**
4. Select this folder: `chrome_extension/`

## Configure
- Extensions page → Details → **Extension options**
- Set the daily time and the template text

## Notes
- Reddit: opens `https://www.reddit.com/submit?...` with title/body prefilled.
- LinkedIn: opens feed + copy-to-clipboard (LinkedIn doesn’t have a stable official prefill URL for text-only posts).

## If you want true auto-posting
Use official APIs:
- Reddit API supports posting via OAuth.
- LinkedIn posting API exists but requires app approval and is harder.

I can scaffold an API-based version next, but it requires OAuth setup and careful policy compliance.
