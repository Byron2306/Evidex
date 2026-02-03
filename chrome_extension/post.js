const DEFAULTS = {
  title: "AI Evidence & Compliance Pack — 48-Hour Delivery",
  body:
    "If you’re up against a donor report or audit: we turn your existing docs into an audit-ready evidence pack in 48 hours. Fixed $500. Confidential/white-label. Reply for the intake link.",
  redditSubmitUrl: "https://www.reddit.com/submit",
  linkedInUrl: "https://www.linkedin.com/feed/"
};

function qs(id) {
  return document.getElementById(id);
}

function buildRedditPrefillUrl(base, title, body) {
  const u = new URL(base);
  // Basic prefill supported by reddit submit.
  u.searchParams.set("title", title);
  u.searchParams.set("text", body);
  return u.toString();
}

async function main() {
  const s = await chrome.storage.sync.get(DEFAULTS);
  qs("title").textContent = s.title;
  qs("body").textContent = s.body;

  qs("copy").addEventListener("click", async () => {
    await navigator.clipboard.writeText(s.body);
    qs("copy").textContent = "Copied";
    setTimeout(() => (qs("copy").textContent = "Copy body"), 1000);
  });

  qs("openReddit").addEventListener("click", async () => {
    const url = buildRedditPrefillUrl(s.redditSubmitUrl, s.title, s.body);
    await chrome.tabs.create({ url });
  });

  qs("openLinkedIn").addEventListener("click", async () => {
    // LinkedIn doesn't provide a stable/official prefill URL for text-only posts.
    // Open feed and rely on clipboard copy.
    await chrome.tabs.create({ url: s.linkedInUrl });
  });
}

document.addEventListener("DOMContentLoaded", main);
