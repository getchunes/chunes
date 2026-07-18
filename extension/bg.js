const PORT = 52846;

async function report() {
  let tabs = [];
  try {
    tabs = await chrome.tabs.query({ audible: true });
  } catch (e) {
    return;
  }
  const body = JSON.stringify(tabs.map((t) => {
    let host = "";
    try { host = new URL(t.url).hostname; } catch (e) {}
    return { host, title: t.title || "" };
  }));
  try {
    await fetch(`http://127.0.0.1:${PORT}/tabs`, { method: "POST", body });
  } catch (e) {
    // presence app not running; nothing to do
  }
}

chrome.alarms.create("poll", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(report);
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if ("audible" in info || "title" in info) report();
});
chrome.tabs.onRemoved.addListener(() => report());
report();
