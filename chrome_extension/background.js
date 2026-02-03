const DEFAULTS = {
  enabled: true,
  hourLocal: 9,
  minuteLocal: 0,
  title: "AI Evidence & Compliance Pack — 48-Hour Delivery",
  body:
    "If you’re up against a donor report or audit: we turn your existing docs into an audit-ready evidence pack in 48 hours. Fixed $500. Confidential/white-label. Reply for the intake link.",
  redditSubmitUrl: "https://www.reddit.com/submit",
  linkedInUrl: "https://www.linkedin.com/feed/"
};

// 1x1 transparent PNG data URL (keeps the extension loadable without binary assets).
const NOTIFICATION_ICON_DATA_URL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/6V9mSoAAAAASUVORK5CYII=";

function pad2(n) {
  return String(n).padStart(2, "0");
}

async function getSettings() {
  const data = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...data };
}

function nextAlarmDelayMs(hour, minute) {
  const now = new Date();
  const next = new Date(now);
  next.setHours(hour, minute, 0, 0);
  if (next <= now) next.setDate(next.getDate() + 1);
  return next.getTime() - now.getTime();
}

async function scheduleDaily() {
  const s = await getSettings();
  await chrome.alarms.clearAll();
  if (!s.enabled) return;

  // Fire once at the next target time, then re-schedule daily after it fires.
  const delayMs = nextAlarmDelayMs(s.hourLocal, s.minuteLocal);
  chrome.alarms.create("daily_outreach", { when: Date.now() + delayMs });
}

chrome.runtime.onInstalled.addListener(scheduleDaily);
chrome.runtime.onStartup.addListener(scheduleDaily);
chrome.storage.onChanged.addListener(scheduleDaily);

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "daily_outreach") return;

  const s = await getSettings();
  if (!s.enabled) return;

  await chrome.notifications.create("daily_outreach_nudge", {
    type: "basic",
    iconUrl: NOTIFICATION_ICON_DATA_URL,
    title: "Post today’s 1 outreach message",
    message: `${pad2(s.hourLocal)}:${pad2(s.minuteLocal)} — click to open the posting helper.`
  });

  // Reschedule for tomorrow.
  await scheduleDaily();
});

chrome.notifications.onClicked.addListener(async (id) => {
  if (id !== "daily_outreach_nudge") return;
  await chrome.tabs.create({ url: chrome.runtime.getURL("post.html") });
});

chrome.action.onClicked.addListener(async () => {
  await chrome.tabs.create({ url: chrome.runtime.getURL("post.html") });
});
