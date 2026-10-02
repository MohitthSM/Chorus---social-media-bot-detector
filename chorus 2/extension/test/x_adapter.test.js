const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const X = require("../src/adapters/x.js");

const fixture = (name) => new JSDOM(fs.readFileSync(path.join(__dirname, "fixtures", name), "utf8")).window.document;

test("thread page: focal post, replies get parent_id, handles lowercased", () => {
  const page = X.extractPage(fixture("x_thread.html"), "/Pol_A/status/100");
  const ids = page.posts.map((p) => p.post_id);
  assert.deepEqual(ids, ["100", "101", "102"]); // ad without timestamp skipped
  const [focal, reply, quoter] = page.posts;
  assert.equal(focal.author, "pol_a");
  assert.equal(focal.parent_id, null);
  assert.equal(reply.parent_id, "100");
  assert.equal(reply.author, "abcde12345678");
  assert.equal(quoter.parent_id, "100");
});

test("emoji images are kept in text; metrics parsed from aria-label", () => {
  const [focal] = X.extractPage(fixture("x_thread.html"), "/Pol_A/status/100").posts;
  assert.equal(focal.text, "Budget announcement today 🇮🇳");
  assert.deepEqual(focal.metrics, { replies: 1204, reposts: 3100, likes: 12000, bookmarks: 40, views: 1200000 });
  assert.equal(focal.created_at, "2026-09-01T10:00:00.000Z");
});

test("quoted post text and author are not attributed to the quoter", () => {
  const quoter = X.extractPage(fixture("x_thread.html"), "/Pol_A/status/100").posts[2];
  assert.equal(quoter.author, "quoter");
  assert.equal(quoter.post_id, "102");
  assert.equal(quoter.text, "");
});

test("home timeline: repost context yields a repost record, 'Pinned' does not", () => {
  const page = X.extractPage(fixture("x_home.html"), "/home");
  assert.deepEqual(page.reposts, [{ actor: "amplifier_1", post_id: "200" }]);
  assert.equal(page.posts.length, 2);
  assert.ok(page.posts.every((p) => p.parent_id === null));
});

test("parseCount and handleFromHref", () => {
  assert.equal(X.parseCount("1.2K"), 1200);
  assert.equal(X.parseCount("3M"), 3000000);
  assert.equal(X.parseCount("1,234"), 1234);
  assert.equal(X.parseCount("abc"), null);
  assert.equal(X.handleFromHref("/Some_User"), "some_user");
  assert.equal(X.handleFromHref("/home"), null);
  assert.equal(X.handleFromHref("/a/status/1"), null);
});

test("currentUser reads the logged-in handle from the side navigation", () => {
  assert.equal(X.currentUser(fixture("x_thread.html")), "quoter");
  assert.equal(X.currentUser(fixture("x_home.html")), null);
});
