"""Analysis pipeline: stored observations -> per-account labels with evidence."""
from __future__ import annotations

import logging
import time
from collections import defaultdict

from . import coordination
from .classifier import Classifier
from .config import Settings
from .coordination import Post
from .db import Store
from .textsim import near_duplicate_groups

log = logging.getLogger("chorus.analysis")

CAVEAT = ("Automated signal from public activity seen by Chorus users. "
          "It is not a verdict about who runs or pays for this account.")


def post_url(platform: str, author: str, post_id: str) -> str | None:
    if platform == "x":
        return f"https://x.com/{author}/status/{post_id}"
    return None


def copypasta(posts: dict[str, Post], dup_groups: list[list[str]], s: Settings) -> dict[str, dict]:
    """Top-level posts that are near-copies of posts by >= 2 other accounts within narrative_window_h.

    Replies are excluded: identical replies are handled by coordination detection, and short
    stock replies ("congratulations on this achievement") are common among ordinary fans.
    """
    window = s.narrative_window_h * 3600
    hits: dict[str, set[str]] = defaultdict(set)
    for g in dup_groups:
        items = sorted((posts[p] for p in g if posts[p].ts is not None and not posts[p].parent_id),
                       key=lambda p: p.ts)
        for p in items:
            others = {q.author for q in items if q.author != p.author and abs(q.ts - p.ts) <= window}
            if len(others) >= 2:
                hits[p.author].add(p.post_id)
    totals: dict[str, int] = defaultdict(int)
    for p in posts.values():
        if not p.parent_id and p.text and len(p.text) >= s.min_text_len:
            totals[p.author] += 1
    return {a: {"count": len(ids), "total": totals[a], "rate": round(len(ids) / max(1, totals[a]), 3),
                "examples": sorted(ids)[:3]}
            for a, ids in hits.items()}


def abuse_rates(posts: dict[str, Post], s: Settings) -> dict[str, dict]:
    n, flagged, ex = defaultdict(int), defaultdict(int), defaultdict(list)
    for p in posts.values():
        if p.abuse is None or p.abuse < 0:
            continue
        n[p.author] += 1
        if p.abuse >= s.abuse_post_threshold:
            flagged[p.author] += 1
            ex[p.author].append((p.abuse, p.post_id))
    return {a: {"scored": n[a], "flagged": flagged[a], "rate": round(flagged[a] / n[a], 3),
                "examples": [pid for _, pid in sorted(ex[a], reverse=True)[:3]]}
            for a in n}


def _level(score: float, s: Settings) -> str:
    return "high" if score >= s.high_threshold else "medium"


def build_scores(platform: str, posts: dict[str, Post], coord: dict, copy: dict, abuse: dict,
                 s: Settings) -> dict[str, dict]:
    seen: dict[str, int] = defaultdict(int)
    for p in posts.values():
        seen[p.author] += 1

    def examples(ids):
        return [{"post_id": i, "text": posts[i].text[:280], "url": post_url(platform, posts[i].author, i)}
                for i in ids if i in posts]

    out = {}
    for handle in set(seen) | set(coord):
        labels, evidence = [], {"posts_seen": seen.get(handle, 0)}

        c = coord.get(handle)
        if c:
            evidence["coordination"] = c | {"examples": examples(c["examples"])}
            if c["score"] >= s.label_threshold:
                k = c["counts"]
                parts = [f"In a {c['cluster_size']}-account cluster."]
                if k["synced_replies"]:
                    parts.append(f"{k['synced_replies']} of {k['replies_seen']} replies landed within "
                                 f"{s.sync_window_s}s of another cluster member's reply to the same post.")
                if k["near_duplicate_posts"]:
                    parts.append(f"{k['near_duplicate_posts']} posts near-identical to other members' posts.")
                if k["coreposts"]:
                    parts.append(f"Reposted {k['coreposts']} of the same posts as other members.")
                if k["distinct_targets"] > 1:
                    parts.append(f"Activity spans {k['distinct_targets']} target accounts.")
                labels.append({"kind": "coordinated", "title": "Coordinated activity",
                               "level": _level(c["score"], s), "score": c["score"], "summary": " ".join(parts)})

        cp = copy.get(handle)
        if cp:
            evidence["copypasta"] = cp | {"examples": examples(cp["examples"])}
            if cp["count"] >= s.copypasta_min_posts and cp["rate"] >= s.copypasta_min_rate:
                labels.append({"kind": "copypasta", "title": "Copy-paste talking points",
                               "level": "high" if cp["rate"] >= 0.6 else "medium", "score": cp["rate"],
                               "summary": f"{cp['count']} of {cp['total']} original posts are near-copies of "
                                          f"posts by 2+ "
                                          f"other accounts within {s.narrative_window_h:g}h."})

        ab = abuse.get(handle)
        if ab:
            evidence["abuse"] = ab | {"examples": examples(ab["examples"])}
            if (ab["scored"] >= s.abuse_min_posts and ab["flagged"] >= s.abuse_min_flagged
                    and ab["rate"] >= s.abuse_rate_label):
                labels.append({"kind": "abusive", "title": "Frequent abusive content",
                               "level": "high" if ab["rate"] >= 0.7 else "medium", "score": ab["rate"],
                               "summary": f"{ab['flagged']} of {ab['scored']} posts seen were classified "
                                          f"abusive or hateful."})

        if labels:
            status = "labeled"
        elif seen.get(handle, 0) >= s.abuse_min_posts:
            status = "no_signal"
        else:
            status = "insufficient_data"
        if status != "insufficient_data" or evidence.keys() - {"posts_seen"}:
            out[handle] = {"status": status, "labels": labels, "evidence": evidence}
    return out


def run_analysis(store: Store, s: Settings, clf: Classifier | None) -> dict:
    t0 = time.time()
    since = t0 - s.retention_days * 86400
    store.prune(since)
    summary = {}
    for platform in store.platforms():
        rows = store.load_posts(platform, s.min_observers, since)

        if clf is not None:
            todo = [(r["post_id"], r["text"]) for r in rows if r["abuse"] is None and r["text"]]
            for i in range(0, len(todo), 256):
                chunk = todo[i:i + 256]
                scores = clf.predict([t for _, t in chunk])
                # -1 marks "unsupported language": scored once, never counted
                store.set_abuse(platform, [(pid, -1.0 if sc is None else sc)
                                           for (pid, _), sc in zip(chunk, scores, strict=True)])
            if todo:
                rows = store.load_posts(platform, s.min_observers, since)

        posts = {r["post_id"]: Post(r["post_id"], r["author"], r["text"] or "", r["created_at"],
                                    r["parent_id"], r["abuse"]) for r in rows}
        reposters: dict[str, set[str]] = defaultdict(set)
        for r in store.load_reposts(platform, s.min_observers):
            reposters[r["post_id"]].add(r["actor"])

        groups = near_duplicate_groups([(p.post_id, p.text) for p in posts.values()], s.dup_jaccard, s.min_text_len)
        clusters, coord = coordination.detect(posts, reposters, groups, s)
        copy = copypasta(posts, groups, s)
        abuse = abuse_rates(posts, s)
        scores = build_scores(platform, posts, coord, copy, abuse, s)
        store.write_results(platform, scores, clusters)
        summary[platform] = {
            "posts": len(posts), "near_dup_groups": len(groups), "clusters": len(clusters),
            "labeled": sum(1 for v in scores.values() if v["status"] == "labeled"),
        }
    summary["seconds"] = round(time.time() - t0, 2)
    log.info("analysis done: %s", summary)
    return summary
