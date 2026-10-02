"""Coordinated-activity detection.

Builds an account-account graph from three kinds of shared behaviour, clusters it with Louvain,
then scores each member on how much of its activity is explained by the cluster.

  synchronized replies  - two accounts reply to the same post within `sync_window_s` of each other
  near-duplicate text   - two accounts post near-identical text (anywhere)
  co-reposts            - two accounts repost the same post

A single signal is never enough for a label. Synchronized replies alone top out at 0.35 and the
default label threshold is 0.5. Clusters that only ever target one account also need near-identical
text before anyone is labeled, because fans of one account look the same on timing and reposts.
"""
from __future__ import annotations

import hashlib
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

import networkx as nx

from .config import Settings

TEMPLATED_HANDLE = re.compile(r"^[a-z_]+\d{6,}$")

WEIGHTS = {"sync": 0.35, "dup": 0.30, "diverse": 0.15, "repost": 0.10, "template": 0.10}


@dataclass
class Post:
    post_id: str
    author: str
    text: str
    ts: float | None
    parent_id: str | None
    abuse: float | None = None


@dataclass
class PairEvidence:
    synced: list = field(default_factory=list)      # (parent_id, reply_a, reply_b, dt)
    dups: list = field(default_factory=list)        # (posts_of_a, posts_of_b) per near-dup group
    coreposts: set = field(default_factory=set)     # post ids

    def shared_posts(self, posts: dict[str, Post]) -> set[str]:
        s = {e[0] for e in self.synced} | set(self.coreposts)
        for pa, pb in self.dups:
            for pid in (*pa, *pb):
                s.add(posts[pid].parent_id or pid)
        return s


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def build_pairs(posts: dict[str, Post], reposters: dict[str, set[str]], dup_groups: list[list[str]],
                s: Settings) -> dict[tuple[str, str], PairEvidence]:
    pairs: dict[tuple[str, str], PairEvidence] = defaultdict(PairEvidence)

    # 1. synchronized replies
    by_parent: dict[str, list[Post]] = defaultdict(list)
    for p in posts.values():
        if p.parent_id and p.ts is not None:
            by_parent[p.parent_id].append(p)
    for parent, replies in by_parent.items():
        replies.sort(key=lambda p: p.ts)
        for i, a in enumerate(replies):
            for b in replies[i + 1:i + 60]:
                dt = b.ts - a.ts
                if dt > s.sync_window_s:
                    break
                if a.author != b.author:
                    key = _pair(a.author, b.author)
                    ra, rb = (a, b) if key[0] == a.author else (b, a)
                    pairs[key].synced.append((parent, ra.post_id, rb.post_id, dt))

    # 2. near-duplicate text
    for g in dup_groups:
        by_author: dict[str, list[str]] = defaultdict(list)
        for pid in g:
            by_author[posts[pid].author].append(pid)
        authors = sorted(by_author)[:300]
        for a, b in combinations(authors, 2):
            pairs[(a, b)].dups.append((tuple(by_author[a]), tuple(by_author[b])))

    # 3. co-reposts
    for pid, actors in reposters.items():
        if 2 <= len(actors) <= s.repost_pair_cap:
            for a, b in combinations(sorted(actors), 2):
                pairs[(a, b)].coreposts.add(pid)
    return pairs


def detect(posts: dict[str, Post], reposters: dict[str, set[str]], dup_groups: list[list[str]],
           s: Settings) -> tuple[list[dict], dict[str, dict]]:
    """Returns (clusters, per_account) where per_account[handle] = {score, components, summary, ...}."""
    pairs = build_pairs(posts, reposters, dup_groups, s)

    g = nx.Graph()
    for (a, b), ev in pairs.items():
        shared = ev.shared_posts(posts)
        if len(shared) >= s.min_shared_posts:
            w = len({e[0] for e in ev.synced}) + len(ev.dups) + 0.5 * len(ev.coreposts)
            g.add_edge(a, b, weight=w)
    if g.number_of_edges() == 0:
        return [], {}

    replies_by_author: dict[str, list[Post]] = defaultdict(list)
    texts_by_author: dict[str, int] = defaultdict(int)
    for p in posts.values():
        if p.parent_id:
            replies_by_author[p.author].append(p)
        if p.text and len(p.text) >= s.min_text_len:
            texts_by_author[p.author] += 1

    clusters, per_account = [], {}
    for comm in nx.community.louvain_communities(g, weight="weight", seed=0):
        if len(comm) < s.min_cluster_size:
            continue
        members = sorted(comm)
        cid = "c_" + hashlib.sha1(",".join(members).encode()).hexdigest()[:10]

        # per-member aggregation of evidence shared with other members
        m_synced_replies = defaultdict(set)
        m_sync_targets = defaultdict(set)
        m_dup_posts = defaultdict(set)
        m_coreposts = defaultdict(set)
        m_shared = defaultdict(set)
        dts, targets, target_posts = [], defaultdict(int), set()
        for a, b in combinations(members, 2):
            ev = pairs.get((a, b))
            if not ev:
                continue
            shared = ev.shared_posts(posts)
            m_shared[a] |= shared
            m_shared[b] |= shared
            for parent, ra, rb, dt in ev.synced:
                m_synced_replies[a].add(ra)
                m_synced_replies[b].add(rb)
                if parent in posts:
                    m_sync_targets[a].add(posts[parent].author)
                    m_sync_targets[b].add(posts[parent].author)
                    targets[posts[parent].author] += 1
                target_posts.add(parent)
                dts.append(dt)
            for pa, pb in ev.dups:
                m_dup_posts[a].update(pa)
                m_dup_posts[b].update(pb)
            m_coreposts[a] |= ev.coreposts
            m_coreposts[b] |= ev.coreposts

        scored = {}
        for m in members:
            n_replies = len(replies_by_author[m])
            comp = {
                "sync": len(m_synced_replies[m]) / n_replies if n_replies else 0.0,
                "dup": min(1.0, len(m_dup_posts[m]) / max(1, texts_by_author[m])),
                "diverse": min(1.0, max(0, len(m_sync_targets[m]) - 1) / 2),
                "repost": min(1.0, len(m_coreposts[m]) / 10),
                "template": 1.0 if TEMPLATED_HANDLE.match(m) else 0.0,
            }
            score = sum(WEIGHTS[k] * v for k, v in comp.items())
            gate = None
            if len(m_shared[m]) < s.min_shared_posts:
                gate = "too_few_shared_posts"
            elif len(m_sync_targets[m]) <= 1 and comp["dup"] < 0.5:
                # Fans of one account reply fast and repost the same posts. Without near-identical
                # text, a single-target cluster is indistinguishable from a fandom, so no label.
                gate = "single_target_without_duplicate_text"
            if gate:
                score = min(score, s.label_threshold - 0.01)
            scored[m] = score
            per_account[m] = {
                "cluster_id": cid,
                "cluster_size": len(members),
                "score": round(score, 3),
                "gate": gate,
                "components": {k: round(v, 3) for k, v in comp.items()},
                "counts": {
                    "replies_seen": n_replies,
                    "synced_replies": len(m_synced_replies[m]),
                    "near_duplicate_posts": len(m_dup_posts[m]),
                    "coreposts": len(m_coreposts[m]),
                    "shared_posts": len(m_shared[m]),
                    "distinct_targets": len(m_sync_targets[m]),
                },
                "examples": sorted(m_dup_posts[m])[:3] or sorted(m_synced_replies[m])[:3],
            }

        dup_posts_all = set().union(*m_dup_posts.values()) if m_dup_posts else set()
        clusters.append({
            "cluster_id": cid,
            "members": members,
            "stats": {
                "size": len(members),
                "edges": g.subgraph(members).number_of_edges(),
                "density": round(nx.density(g.subgraph(members)), 3),
                "target_posts": len(target_posts),
                "top_targets": sorted(targets, key=targets.get, reverse=True)[:5],
                "median_sync_gap_s": round(statistics.median(dts), 1) if dts else None,
                "near_duplicate_posts": len(dup_posts_all),
                "labeled_members": sum(1 for m in members if scored[m] >= s.label_threshold),
                "mean_score": round(sum(scored.values()) / len(scored), 3),
            },
        })
    return clusters, per_account
