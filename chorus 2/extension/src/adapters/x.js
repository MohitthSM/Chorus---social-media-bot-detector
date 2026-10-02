// DOM adapter for X (x.com / twitter.com).
// Reads only what the user is already looking at. X changes its markup often; everything here
// keys off data-testid attributes, which have been the most stable hooks. If extraction breaks,
// this file is the only place to fix.
(function (root) {
  "use strict";

  const HANDLE_PATH = /^\/([A-Za-z0-9_]{1,15})\/?$/;
  const STATUS_PATH = /^\/([A-Za-z0-9_]{1,15})\/status\/(\d{1,25})/;
  const RESERVED = new Set([
    "home", "explore", "notifications", "messages", "search", "settings", "i", "compose",
    "login", "signup", "tos", "privacy", "hashtag", "jobs", "premium",
  ]);

  function pathOf(href) {
    try { return new URL(href, "https://x.com").pathname; } catch (_) { return ""; }
  }

  function handleFromHref(href) {
    const m = HANDLE_PATH.exec(pathOf(href));
    if (!m || RESERVED.has(m[1].toLowerCase())) return null;
    return m[1].toLowerCase();
  }

  // True if `el` sits inside an embedded quote-post within `article`.
  function inQuote(el, article) {
    for (let n = el.parentElement; n && n !== article; n = n.parentElement) {
      if (n.getAttribute && n.getAttribute("role") === "link" && n.querySelector('[data-testid="User-Name"]')) {
        return true;
      }
    }
    return false;
  }

  function first(article, selector) {
    for (const el of article.querySelectorAll(selector)) {
      if (!inQuote(el, article)) return el;
    }
    return null;
  }

  function parseCount(s) {
    const m = /^([\d.,]+)\s*([KMB])?$/i.exec(String(s).trim());
    if (!m) return null;
    const n = parseFloat(m[1].replace(/,/g, ""));
    const mult = { K: 1e3, M: 1e6, B: 1e9 }[(m[2] || "").toUpperCase()] || 1;
    return Math.round(n * mult);
  }

  // e.g. aria-label="12 replies, 34 reposts, 1.2K likes, 8 bookmarks, 9012 views"
  function parseMetrics(article) {
    const group = first(article, '[role="group"][aria-label]');
    if (!group) return null;
    const out = {};
    const re = /([\d.,]+[KMB]?)\s+(repl|repost|like|bookmark|view)/gi;
    let m;
    while ((m = re.exec(group.getAttribute("aria-label")))) {
      const key = { repl: "replies", repost: "reposts", like: "likes", bookmark: "bookmarks", view: "views" }[m[2].toLowerCase()];
      const v = parseCount(m[1]);
      if (v !== null) out[key] = v;
    }
    return Object.keys(out).length ? out : null;
  }

  // Text nodes plus emoji, which X renders as <img alt="😡">
  function textOf(el) {
    if (!el) return "";
    let out = "";
    const walk = (node) => {
      for (const c of node.childNodes) {
        if (c.nodeType === 3) out += c.nodeValue;
        else if (c.nodeType === 1) {
          if (c.tagName === "IMG") out += c.getAttribute("alt") || "";
          else if (c.tagName === "BR") out += "\n";
          else walk(c);
        }
      }
    };
    walk(el);
    return out.trim().slice(0, 5000);
  }

  function extractArticle(article) {
    const time = first(article, "time[datetime]");
    const link = time && time.closest("a[href]");
    if (!link) return null; // ads and some placeholders have no timestamp link
    const m = STATUS_PATH.exec(pathOf(link.getAttribute("href")));
    if (!m) return null;

    const nameBox = first(article, '[data-testid="User-Name"]');
    let displayName = null;
    if (nameBox) {
      const span = nameBox.querySelector("span");
      displayName = span ? span.textContent.trim().slice(0, 100) : null;
    }

    const rec = {
      post_id: m[2],
      author: m[1].toLowerCase(),
      display_name: displayName,
      text: textOf(first(article, '[data-testid="tweetText"]')),
      created_at: time.getAttribute("datetime"),
      parent_id: null,
      metrics: parseMetrics(article),
    };

    let repostedBy = null;
    const ctx = article.querySelector('[data-testid="socialContext"]');
    if (ctx && /repost|retweet/i.test(ctx.textContent)) {
      const a = ctx.closest("a[href]") || ctx.querySelector("a[href]");
      const h = a && handleFromHref(a.getAttribute("href"));
      if (h && h !== rec.author) repostedBy = h;
    }
    return { post: rec, repostedBy };
  }

  // Extract everything visible. On a status page, articles after the focal post are replies to it.
  function extractPage(doc, pathname) {
    const focal = STATUS_PATH.exec(pathname || "");
    const focalId = focal ? focal[2] : null;
    const articles = Array.from(doc.querySelectorAll('article[data-testid="tweet"]'));
    const recs = [];
    let focalEl = null;
    for (const art of articles) {
      const r = extractArticle(art);
      if (!r) continue;
      r.el = art;
      if (r.post.post_id === focalId) focalEl = art;
      recs.push(r);
    }
    const FOLLOWING = 4; // Node.DOCUMENT_POSITION_FOLLOWING
    const posts = [];
    const reposts = [];
    for (const r of recs) {
      if (focalEl && r.el !== focalEl && (focalEl.compareDocumentPosition(r.el) & FOLLOWING)) {
        r.post.parent_id = focalId;
      }
      posts.push(r.post);
      if (r.repostedBy) reposts.push({ actor: r.repostedBy, post_id: r.post.post_id });
    }
    return { platform: "x", posts, reposts, articles: recs.map((r) => ({ el: r.el, author: r.post.author, post_id: r.post.post_id, isReply: !!r.post.parent_id })) };
  }

  // Where to put the badge for an article
  function badgeAnchor(article) {
    return first(article, '[data-testid="User-Name"]');
  }

  // Where to put the evidence panel: below the post text, so it gets the full column width
  function panelAnchor(article) {
    return first(article, '[data-testid="tweetText"]') || badgeAnchor(article);
  }

  // The logged-in user's handle, from the "Profile" link in X's side navigation
  function currentUser(doc) {
    const a = doc.querySelector('a[data-testid="AppTabBar_Profile_Link"][href]');
    return a ? handleFromHref(a.getAttribute("href")) : null;
  }

  const api = {
    extractPage, extractArticle, parseCount, parseMetrics, handleFromHref, badgeAnchor, panelAnchor, currentUser,
  };
  root.ChorusX = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
