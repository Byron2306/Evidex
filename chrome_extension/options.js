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

function el(id) {
  return document.getElementById(id);
}

async function load() {
  const s = await chrome.storage.sync.get(DEFAULTS);
  el("enabled").checked = !!s.enabled;
  el("hourLocal").value = s.hourLocal;
  el("minuteLocal").value = s.minuteLocal;
  el("title").value = s.title;
  el("body").value = s.body;
  el("redditSubmitUrl").value = s.redditSubmitUrl;
  el("linkedInUrl").value = s.linkedInUrl;
}

async function save() {
  const s = {
    enabled: el("enabled").checked,
    hourLocal: Math.min(23, Math.max(0, Number(el("hourLocal").value || 0))),
    minuteLocal: Math.min(59, Math.max(0, Number(el("minuteLocal").value || 0))),
    title: String(el("title").value || "").trim(),
    body: String(el("body").value || "").trim(),
    redditSubmitUrl: String(el("redditSubmitUrl").value || "").trim(),
    linkedInUrl: String(el("linkedInUrl").value || "").trim()
  };
  await chrome.storage.sync.set(s);
  const status = el("status");
  status.textContent = "Saved";
  setTimeout(() => (status.textContent = ""), 1200);
}

document.addEventListener("DOMContentLoaded", load);
el("save").addEventListener("click", save);
