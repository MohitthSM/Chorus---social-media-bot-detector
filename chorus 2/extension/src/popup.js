"use strict";

const $ = (id) => document.getElementById(id);
const send = (msg) => new Promise((r) => chrome.runtime.sendMessage(msg, r));

function setStatus(text, cls) {
  const s = $("status");
  s.textContent = text;
  s.className = cls || "";
}

async function refreshStatus() {
  const h = await send({ type: "health" });
  if (!h || !h.ok) return setStatus("Backend unreachable. Is it running? " + ((h && h.error) || ""), "bad");
  const st = await send({ type: "stats" });
  const d = st && st.ok ? st.data : {};
  const clf = h.data.classifier || "none (abuse labels off)";
  setStatus(`Connected. Classifier: ${clf}. ${d.posts ?? "?"} posts, ${d.accounts ?? "?"} accounts, ` +
            `${d.clusters ?? "?"} clusters, ${d.labeled_accounts ?? "?"} labeled.`, "ok");
}

async function load() {
  const res = await send({ type: "settings" });
  const s = res && res.ok ? res.data : {};
  for (const k of ["enabled", "contribute", "collapseFlagged", "testMode"]) $(k).checked = !!s[k];
  $("backendUrl").value = s.backendUrl || "";
  $("testHandle").value = s.testHandle ? "@" + s.testHandle : "";
  refreshStatus();
}

$("save").addEventListener("click", async () => {
  let url = $("backendUrl").value.trim().replace(/\/$/, "");
  try { url = new URL(url).origin; } catch (_) { return setStatus("Backend URL is not a valid URL.", "bad"); }
  // localhost is granted in the manifest; any other backend needs an explicit permission grant
  if (!/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(url)) {
    const granted = await chrome.permissions.request({ origins: [url + "/*"] });
    if (!granted) return setStatus("Permission to reach that backend was not granted.", "bad");
  }
  await chrome.storage.sync.set({
    enabled: $("enabled").checked,
    contribute: $("contribute").checked,
    collapseFlagged: $("collapseFlagged").checked,
    testMode: $("testMode").checked,
    testHandle: $("testHandle").value.trim().replace(/^@/, "").toLowerCase(),
    backendUrl: url,
  });
  setStatus("Saved.", "ok");
  setTimeout(refreshStatus, 300);
});

$("analyze").addEventListener("click", async () => {
  setStatus("Analyzing…");
  const r = await send({ type: "analyze" });
  if (!r || !r.ok) return setStatus("Analysis failed: " + ((r && r.error) || "no response"), "bad");
  const x = r.data.x || {};
  setStatus(`Done in ${r.data.seconds}s: ${x.posts ?? 0} posts, ${x.clusters ?? 0} clusters, ${x.labeled ?? 0} labeled.`, "ok");
});

load();
