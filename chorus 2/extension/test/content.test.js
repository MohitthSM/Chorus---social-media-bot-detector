// Runs the real content script in jsdom against a mocked chrome.* API.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const src = (p) => fs.readFileSync(path.join(__dirname, "..", "src", p), "utf8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const XSS = '<img src=x onerror="window.__pwned=1">';

function setup(t, settings = {}) {
  const html = fs.readFileSync(path.join(__dirname, "fixtures", "x_thread.html"), "utf8");
  const dom = new JSDOM(html, { url: "https://x.com/Pol_A/status/100", runScripts: "outside-only" });
  const { window } = dom;
  t.after(() => window.close()); // stops the content script's timers
  const calls = [];
  window.chrome = {
    runtime: {
      lastError: undefined,
      sendMessage(msg, cb) {
        calls.push(msg);
        let data = {};
        if (msg.type === "settings") data = { enabled: true, contribute: true, collapseFlagged: true, ...settings };
        if (msg.type === "scores") {
          data = { scores: {} };
          for (const h of msg.handles) {
            data.scores[h] = h === "abcde12345678"
              ? {
                status: "labeled",
                labels: [{ kind: "coordinated", title: "Coordinated activity", level: "high", score: 0.9, summary: "In a 12-account cluster." }],
                evidence: { coordination: { examples: [{ post_id: "101", text: XSS, url: "https://x.com/abcde12345678/status/101" }] } },
                caveat: "Automated signal.",
              }
              : { status: "no_signal", labels: [], evidence: {} };
          }
        }
        setTimeout(() => cb({ ok: true, data }), 0);
      },
    },
    storage: { onChanged: { addListener() {} } },
  };
  window.eval(src("adapters/x.js"));
  window.eval(src("content.js"));
  return { window, calls };
}

test("badges only the labeled account, next to its name", async (t) => {
  const { window } = setup(t);
  await sleep(700);
  const badges = window.document.querySelectorAll(".chorus-badge");
  assert.equal(badges.length, 1);
  assert.equal(badges[0].dataset.handle, "abcde12345678");
  assert.match(badges[0].textContent, /Coordinated/);
  assert.ok(badges[0].classList.contains("chorus-high"));
  // high-level reply is blurred when collapseFlagged is on
  assert.ok(badges[0].closest("article").classList.contains("chorus-collapsed"));
});

test("evidence panel renders untrusted text as text, not HTML", async (t) => {
  const { window } = setup(t);
  await sleep(700);
  window.document.querySelector(".chorus-badge").click();
  const panel = window.document.querySelector(".chorus-panel");
  assert.ok(panel, "panel opens on click");
  const text = panel.closest("article").querySelector('[data-testid="tweetText"]');
  assert.equal(text.nextElementSibling, panel, "panel sits below the post text");
  assert.equal(panel.querySelector("img"), null);
  assert.ok(panel.textContent.includes(XSS));
  assert.equal(window.__pwned, undefined);
});

test("observations are batched to the backend with reply structure", async (t) => {
  const { calls } = setup(t);
  await sleep(4500);
  const obs = calls.filter((c) => c.type === "observe");
  assert.equal(obs.length, 1);
  // JSON round-trip: arrays built inside jsdom have a different realm prototype
  assert.deepEqual(JSON.parse(JSON.stringify(obs[0].posts.map((p) => [p.post_id, p.parent_id]))), [["100", null], ["101", "100"], ["102", "100"]]);
});

test("disabled extension shows nothing", async (t) => {
  const { window } = setup(t, { enabled: false });
  await sleep(700);
  assert.equal(window.document.querySelectorAll(".chorus-badge").length, 0);
});

test("test mode: local test badge on own account, seen dots, status box; nothing sent about it", async (t) => {
  const { window, calls } = setup(t, { testMode: true });
  await sleep(700);
  const doc = window.document;
  const test = doc.querySelector('.chorus-badge[data-handle="quoter"]');
  assert.ok(test, "test badge on the logged-in user's post");
  assert.ok(test.classList.contains("chorus-test"));
  assert.match(test.textContent, /Test/);
  // the real label is still shown for the flagged account
  assert.ok(doc.querySelector('.chorus-badge[data-handle="abcde12345678"].chorus-high'));
  assert.equal(doc.querySelectorAll(".chorus-seen").length, 3);
  const hud = doc.querySelector(".chorus-hud");
  assert.match(hud.textContent, /Posts read in this tab: 3/);
  assert.match(hud.textContent, /Test badge on: @quoter/);
  test.click();
  const panel = doc.querySelector(".chorus-panel");
  assert.match(panel.textContent, /only in your browser/);
  assert.equal(panel.querySelector(".chorus-panel-appeal"), null, "no appeal button on a test badge");
  assert.ok(!calls.some((c) => c.type === "appeal"));
});

test("test mode honours an explicit handle and is off by default", async (t) => {
  const on = setup(t, { testMode: true, testHandle: "pol_a" });
  await sleep(700);
  assert.ok(on.window.document.querySelector('.chorus-badge[data-handle="pol_a"].chorus-test'));
  assert.equal(on.window.document.querySelector('.chorus-badge[data-handle="quoter"]'), null);

  const off = setup(t);
  await sleep(700);
  const d = off.window.document;
  assert.equal(d.querySelector(".chorus-test"), null);
  assert.equal(d.querySelector(".chorus-seen"), null);
  assert.equal(d.querySelector(".chorus-hud"), null);
});
