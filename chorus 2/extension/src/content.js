// Content script: extract what is on screen, send it to the backend, and badge flagged accounts.
// All backend text is inserted with textContent (never innerHTML): evidence includes other people's posts.
(function () {
  "use strict";
  const adapter = globalThis.ChorusX;
  if (!adapter) return;

  const FLUSH_MS = 4000;
  const seen = new Set();        // post_id|parent_id already queued
  const queue = { posts: [], reposts: [] };
  const scores = new Map();      // handle -> score object
  const requested = new Set();   // handles with a score request in flight or done
  let settings = { enabled: true, collapseFlagged: false, testMode: false, testHandle: "" };
  const stats = { read: 0, sent: 0, queued: 0, backend: "not contacted yet" };

  // Test mode: a local-only badge on your own account so you can check the UI on live X.
  // It is built here, never sent to or stored by the backend.
  const TEST_SCORE = {
    test: true,
    status: "labeled",
    labels: [{ kind: "test", title: "Chorus test badge", level: "test", score: 0,
      summary: "Test mode is on. This badge is only on your own account, only in your browser, " +
               "and says nothing about the account. Turn test mode off in the Chorus popup." }],
    evidence: {},
    caveat: "Not a real signal.",
  };

  const send = (msg) => new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (res) => resolve(chrome.runtime.lastError ? null : res));
    } catch (_) { resolve(null); } // extension reloaded; this page's script is orphaned
  });

  function scan() {
    if (!settings.enabled) return;
    const page = adapter.extractPage(document, location.pathname);
    for (const p of page.posts) {
      const key = p.post_id + "|" + (p.parent_id || "");
      if (seen.has(key)) continue;
      seen.add(key);
      queue.posts.push(p);
      stats.read++;
    }
    for (const r of page.reposts) {
      const key = "r|" + r.actor + "|" + r.post_id;
      if (seen.has(key)) continue;
      seen.add(key);
      queue.reposts.push(r);
    }
    const handles = new Set(page.articles.map((a) => a.author));
    const want = [...handles].filter((h) => !requested.has(h));
    if (want.length) requestScores(want);
    decorate(page.articles);
  }

  async function requestScores(handles) {
    handles.forEach((h) => requested.add(h));
    const res = await send({ type: "scores", platform: "x", handles });
    if (!res || !res.ok) {
      handles.forEach((h) => requested.delete(h)); // retry on next scan
      return;
    }
    for (const [h, sc] of Object.entries(res.data.scores)) scores.set(h, sc);
    scan();
  }

  async function flush() {
    if (!queue.posts.length && !queue.reposts.length) return;
    const batch = { type: "observe", platform: "x", posts: queue.posts.splice(0), reposts: queue.reposts.splice(0) };
    const res = await send(batch);
    if (!res || !res.ok) { // put back and try later
      queue.posts.unshift(...batch.posts);
      queue.reposts.unshift(...batch.reposts);
      stats.backend = "error: " + ((res && res.error) || "no response");
    } else if (res.data && res.data.skipped) {
      stats.backend = "sharing is off";
    } else {
      stats.sent += batch.posts.length;
      stats.backend = "ok";
    }
    updateHud();
  }

  // ---------- UI ----------
  const LEVEL_RANK = { high: 2, medium: 1 };
  const SHORT = { coordinated: "Coordinated", copypasta: "Copy-paste", abusive: "Abusive", test: "Test" };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function topLevel(labels) {
    return labels.reduce((m, l) => Math.max(m, LEVEL_RANK[l.level] || 0), 0) === 2 ? "high" : "medium";
  }

  function buildPanel(handle, sc) {
    const panel = el("div", "chorus-panel");
    panel.appendChild(el("div", "chorus-panel-title", "Chorus signals for @" + handle));
    for (const l of sc.labels) {
      const row = el("div", "chorus-panel-label");
      row.appendChild(el("strong", "", `${l.title} (${l.level})`));
      row.appendChild(el("div", "", l.summary));
      panel.appendChild(row);
    }
    // examples only for signals that produced a label
    const EVIDENCE_KEY = { coordinated: "coordination", copypasta: "copypasta", abusive: "abuse" };
    const ex = [];
    for (const l of sc.labels) {
      for (const e of ((sc.evidence[EVIDENCE_KEY[l.kind]] || {}).examples || [])) ex.push(e);
    }
    if (ex.length) {
      panel.appendChild(el("div", "chorus-panel-sub", "Example posts"));
      const ul = el("ul");
      for (const e of ex.slice(0, 4)) {
        const li = el("li");
        if (e.url) {
          const a = el("a", "", e.text || "(no text)");
          a.href = e.url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          li.appendChild(a);
        } else li.textContent = e.text;
        ul.appendChild(li);
      }
      panel.appendChild(ul);
    }
    panel.appendChild(el("div", "chorus-panel-caveat", sc.caveat || ""));
    for (const ev of ["click", "mousedown", "mouseup"]) panel.addEventListener(ev, (e) => e.stopPropagation());
    if (sc.test) return panel;
    const btn = el("button", "chorus-panel-appeal", "Report a mistake");
    btn.addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const message = prompt(`Why is the label on @${handle} wrong? (sent to the Chorus backend for review)`);
      if (!message) return;
      const res = await send({ type: "appeal", platform: "x", handle, message });
      btn.textContent = res && res.ok ? "Report sent" : "Could not send";
      btn.disabled = true;
    });
    panel.appendChild(btn);
    return panel;
  }

  function testHandle() {
    return (settings.testHandle || adapter.currentUser(document) || "").replace(/^@/, "").toLowerCase();
  }

  function scoreFor(author) {
    const sc = scores.get(author);
    if (sc && sc.status === "labeled" && sc.labels.length) return sc;
    if (settings.testMode && author && author === testHandle()) return TEST_SCORE;
    return null;
  }

  function markSeen(art) {
    if (!settings.testMode || art.querySelector(":scope .chorus-seen")) return;
    const anchor = adapter.badgeAnchor(art);
    if (!anchor) return;
    const dot = el("span", "chorus-seen", "●");
    dot.title = "Read by Chorus";
    anchor.parentElement.insertBefore(dot, anchor.nextSibling);
  }

  function decorate(articles) {
    for (const { el: art, author, isReply } of articles) {
      markSeen(art);
      const sc = scoreFor(author);
      const labeled = !!sc;
      const existing = art.querySelector(":scope .chorus-badge");
      if (!labeled) {
        if (existing) existing.remove();
        art.classList.remove("chorus-collapsed");
        continue;
      }
      if (existing && existing.dataset.handle === author && existing.dataset.test === String(!!sc.test)) continue;
      if (existing) existing.remove();
      const anchor = adapter.badgeAnchor(art);
      if (!anchor) continue;

      const level = sc.test ? "test" : topLevel(sc.labels);
      const badge = el("span", `chorus-badge chorus-${level}`,
        "⚑ " + sc.labels.map((l) => SHORT[l.kind] || l.kind).join(" · "));
      badge.dataset.handle = author;
      badge.dataset.test = String(!!sc.test);
      badge.title = sc.labels.map((l) => `${l.title}: ${l.summary}`).join("\n");
      badge.setAttribute("role", "button");
      badge.tabIndex = 0;
      badge.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        art.classList.remove("chorus-collapsed");
        const open = art.querySelector(":scope .chorus-panel");
        if (open) open.remove();
        else adapter.panelAnchor(art).after(buildPanel(author, sc));
      });
      anchor.parentElement.insertBefore(badge, anchor.nextSibling);
      if (settings.collapseFlagged && isReply && level === "high") art.classList.add("chorus-collapsed");
    }
    updateHud(articles);
  }

  // Test mode: a small status box in the corner so you can see coverage without DevTools
  let hud = null;
  function updateHud(articles) {
    if (!settings.testMode || !settings.enabled) {
      if (hud) { hud.remove(); hud = null; }
      return;
    }
    if (!hud) {
      hud = el("div", "chorus-hud");
      document.body.appendChild(hud);
    }
    if (articles) stats.onScreen = articles.length;
    const flagged = [...scores.values()].filter((s) => s.status === "labeled").length;
    const me = testHandle();
    const text = [
      "Chorus test mode",
      `Posts read in this tab: ${stats.read} (${stats.onScreen || 0} on screen)`,
      `Sent to backend: ${stats.sent}, waiting: ${queue.posts.length}`,
      `Backend: ${stats.backend}`,
      `Flagged accounts seen: ${flagged}`,
      `Test badge on: ${me ? "@" + me : "(your handle not found, set it in the popup)"}`,
    ].join("\n");
    if (hud.textContent !== text) hud.textContent = text; // avoid needless DOM mutations
  }

  // ---------- wiring ----------
  let pending = false;
  const schedule = () => {
    if (pending) return;
    pending = true;
    setTimeout(() => { pending = false; scan(); }, 400);
  };

  function refresh() {
    requested.clear();
    scores.clear();
    document.querySelectorAll(".chorus-seen").forEach((n) => n.remove());
    document.querySelectorAll('.chorus-badge[data-handle]').forEach((n) => n.remove());
    if (!settings.enabled) {
      document.querySelectorAll(".chorus-badge, .chorus-panel").forEach((n) => n.remove());
      document.querySelectorAll(".chorus-collapsed").forEach((n) => n.classList.remove("chorus-collapsed"));
    }
    schedule();
  }

  send({ type: "settings" }).then((res) => {
    if (res && res.ok) settings = res.data;
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
    setInterval(flush, FLUSH_MS);
    // scores change as the backend re-analyzes; refresh what is on screen every few minutes
    setInterval(() => { requested.clear(); schedule(); }, 5 * 60 * 1000);
    schedule();
  });

  chrome.storage.onChanged.addListener((_changes, area) => {
    if (area !== "sync") return;
    send({ type: "settings" }).then((res) => {
      if (res && res.ok) settings = res.data;
      refresh();
    });
  });
})();
