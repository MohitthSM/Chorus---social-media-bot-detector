// Service worker: the only part of the extension that talks to the backend.
"use strict";

const DEFAULTS = {
  backendUrl: "http://localhost:8000",
  enabled: true,
  contribute: true,        // send what you see to the backend so it can detect clusters
  collapseFlagged: false,  // dim replies from accounts with a high-level label
  testMode: false,         // local test badge on your own account + coverage markers
  testHandle: "",          // empty = detect from X's side navigation
};
const SCORE_TTL_MS = 5 * 60 * 1000;
const scoreCache = new Map(); // "platform:handle" -> {at, score}

async function settings() {
  return Object.assign({}, DEFAULTS, await chrome.storage.sync.get(Object.keys(DEFAULTS)));
}

async function clientId() {
  const { clientId } = await chrome.storage.local.get("clientId");
  if (clientId) return clientId;
  const id = crypto.randomUUID().replace(/-/g, "");
  await chrome.storage.local.set({ clientId: id });
  return id;
}

async function api(path, body) {
  const { backendUrl } = await settings();
  const res = await fetch(backendUrl.replace(/\/$/, "") + path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json();
}

async function observe({ platform, posts, reposts }) {
  const s = await settings();
  if (!s.enabled || !s.contribute) return { skipped: true };
  const id = await clientId();
  let sent = 0;
  for (let i = 0; i < Math.max(posts.length, reposts.length); i += 500) {
    await api("/v1/observations", {
      client_id: id, platform,
      posts: posts.slice(i, i + 500), reposts: reposts.slice(i, i + 500),
    });
    sent += posts.slice(i, i + 500).length;
  }
  return { sent };
}

async function scores({ platform, handles }) {
  const now = Date.now();
  const out = {};
  const missing = [];
  for (const h of handles) {
    const c = scoreCache.get(`${platform}:${h}`);
    if (c && now - c.at < SCORE_TTL_MS) out[h] = c.score;
    else missing.push(h);
  }
  for (let i = 0; i < missing.length; i += 200) {
    const res = await api("/v1/scores", { platform, handles: missing.slice(i, i + 200) });
    for (const [h, sc] of Object.entries(res.scores)) {
      scoreCache.set(`${platform}:${h}`, { at: now, score: sc });
      out[h] = sc;
    }
  }
  return { scores: out };
}

const handlers = {
  observe,
  scores,
  appeal: (m) => api("/v1/appeals", { platform: m.platform, handle: m.handle, message: m.message, contact: m.contact || null }),
  health: () => api("/health"),
  stats: () => api("/v1/stats"),
  analyze: async () => { scoreCache.clear(); return api("/v1/analyze", {}); },
  settings: () => settings(),
};

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const fn = handlers[msg && msg.type];
  if (!fn) return false;
  Promise.resolve(fn(msg))
    .then((data) => sendResponse({ ok: true, data }))
    .catch((err) => sendResponse({ ok: false, error: String(err && err.message || err) }));
  return true; // async response
});

chrome.storage.onChanged.addListener(() => scoreCache.clear());
