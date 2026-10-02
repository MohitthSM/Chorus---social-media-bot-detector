"""Synthetic social-graph generator with known ground truth.

Used by the tests and by scripts/simulate.py to check detection quality end to end. It includes
hard negatives (fast-replying fans who all repost the same celebrity, organic users with
auto-generated digit handles) so that a detector which flags "fast + similar" alone will fail.

Abusive text is represented by the placeholder token <abuse>; `KeywordClassifier` flags it.
Real classifier quality is measured separately on real datasets by scripts/train_baseline.py.
"""
from __future__ import annotations

import random
import uuid
from collections import defaultdict
from datetime import datetime, timezone

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
DAY = 86400

EXPECTED = {
    "ring_a": "coordinated",    # templated handles, near-duplicate replies, synced, co-reposts, 3 targets
    "ring_b": "coordinated",    # LLM-style: varied text, but synced + co-reposts across 3 targets
    "ring_c": None,             # varied text, synced, one target, no reposts: known miss (see README)
    "campaign": "copypasta",    # same talking points posted within hours
    "abusive": "abusive",
    "fan": None,                # hard negative: fast replies + co-reposts on one celebrity
    "organic": None,
    "target": None,
}

RING_TEMPLATES = [
    "this government has completely failed the people of this country",
    "everyone must see the truth about what they are hiding from us",
    "shameful behaviour from the opposition once again wake up people",
    "they will never tell you this but the numbers do not lie",
    "stand with the nation and reject these traitors today",
]
TALKING_POINTS = [
    "the new water tariff will hurt every farmer in the state and nobody is talking about it",
    "why is the media silent about the bridge contract scandal that cost us crores",
    "the rally yesterday was the biggest this city has ever seen and the press ignored it",
    "fuel prices were lower under the previous government and everyone remembers it",
    "our leader delivered on every promise made in the manifesto check the record",
    "the opposition candidate has no plan for jobs and the youth deserve better",
]


class KeywordClassifier:
    name = "keyword-stub"

    def predict(self, texts):
        return [0.95 if "<abuse>" in t else 0.05 for t in texts]


def _vocab(rng: random.Random, n=800):
    syl = ["ka", "ro", "mi", "ta", "shu", "ven", "lo", "pra", "dee", "zan", "qui", "el", "om", "bar", "ni",
           "go", "ya", "tri", "sel", "mun", "ha", "po", "rik", "da"]
    return list({"".join(rng.choice(syl) for _ in range(rng.randint(1, 3))) for _ in range(n)})


def generate(seed: int = 7, n_organic: int = 300, days: int = 7):
    rng = random.Random(seed)
    vocab = _vocab(rng)
    truth: dict[str, str] = {}
    posts: list[dict] = []
    reposts: list[dict] = []
    pid = [10**15]

    def new_id():
        pid[0] += rng.randint(1, 1000)
        return str(pid[0])

    def words(n1=8, n2=20):
        return " ".join(rng.choice(vocab) for _ in range(rng.randint(n1, n2)))

    def handle(prefix, templated=False):
        base = prefix + "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(5))
        return base + (str(rng.randint(10**7, 10**8)) if templated else "")

    def add(author, text, ts, parent=None):
        p = {"post_id": new_id(), "author": author, "text": text, "ts": ts, "parent_id": parent}
        posts.append(p)
        return p

    def group(name, n, templated=False, prefix="u"):
        hs = [handle(prefix, templated) for _ in range(n)]
        for h in hs:
            truth[h] = name
        return hs

    targets = ["pol_a", "pol_b", "pol_c", "celeb_d", "news_e", "pol_f", "creator_g", "creator_h"]
    for t in targets:
        truth[t] = "target"
    target_posts = defaultdict(list)
    for t in targets:
        for _ in range(20):
            target_posts[t].append(add(t, words(), T0 + rng.uniform(0, days * DAY)))

    organic = group("organic", n_organic)
    # ~15% of organic users have platform-default "name + digits" handles
    for i in range(0, n_organic, 7):
        new = handle("u", templated=True)
        truth.pop(organic[i])
        organic[i] = new
        truth[new] = "organic"
    fans = group("fan", 30)
    ring_a = group("ring_a", 12, templated=True, prefix="")
    ring_b = group("ring_b", 8)
    ring_c = group("ring_c", 8)
    campaign = group("campaign", 10)
    abusive = group("abusive", 6)

    all_targets = [p for ps in target_posts.values() for p in ps]

    # organic replies/reposts/top-level posts
    for h in organic:
        for p in rng.sample(all_targets, rng.randint(2, 12)):
            txt = words() + (" <abuse>" if rng.random() < 0.03 else "")
            add(h, txt, p["ts"] + rng.lognormvariate(7.8, 1.2), p["post_id"])
        for p in rng.sample(all_targets, rng.randint(0, 4)):
            reposts.append({"actor": h, "post_id": p["post_id"]})
        for _ in range(rng.randint(0, 3)):
            add(h, words(), T0 + rng.uniform(0, days * DAY))

    # fans of celeb_d: fast replies, heavy co-reposting, a common congratulatory phrase
    for h in fans:
        for p in target_posts["celeb_d"]:
            if rng.random() < 0.6:
                txt = "congratulations on this amazing achievement" if rng.random() < 0.1 else words()
                add(h, txt, p["ts"] + rng.expovariate(1 / 60), p["post_id"])
            if rng.random() < 0.7:
                reposts.append({"actor": h, "post_id": p["post_id"]})

    def ring(members, tgt_accounts, n_posts, text_fn, repost):
        chosen = rng.sample([p for t in tgt_accounts for p in target_posts[t]], n_posts)
        for p in chosen:
            start = p["ts"] + rng.uniform(300, 3 * 3600)
            for h in members:
                if rng.random() < 0.85:
                    add(h, text_fn(), start + rng.uniform(0, 90), p["post_id"])
                    if repost:
                        reposts.append({"actor": h, "post_id": p["post_id"]})
        # members also do a little unrelated activity
        for h in members:
            for p in rng.sample(all_targets, 2):
                add(h, words(), p["ts"] + rng.lognormvariate(7.8, 1.2), p["post_id"])

    def templated_text():
        t = rng.choice(RING_TEMPLATES)
        if rng.random() < 0.5:
            t += " #wakeup" + str(rng.randint(1, 9))
        if rng.random() < 0.3:
            t = "@" + rng.choice(targets) + " " + t
        return t + rng.choice(["", " !!", " 🇮🇳", " 😡"])

    ring(ring_a, ["pol_a", "pol_b", "pol_c"], 15, templated_text, repost=True)
    ring(ring_b, ["pol_b", "news_e", "creator_g"], 15, words, repost=True)
    ring(ring_c, ["pol_f"], 15, words, repost=False)

    # copy-paste campaign: each talking point posted by most members within ~2h
    for i, tp in enumerate(TALKING_POINTS):
        t = T0 + (i + 0.5) * DAY
        for h in campaign:
            if rng.random() < 0.9:
                pre = rng.choice(["", "must read ", "important ", "share this "])
                add(h, pre + tp + rng.choice(["", " #truth", " #jaagoindia", " 🙏"]), t + rng.uniform(0, 7200))
    for h in campaign:
        for _ in range(4):
            add(h, words(), T0 + rng.uniform(0, days * DAY))

    # abusive accounts
    for h in abusive:
        for p in rng.sample(all_targets, 10):
            txt = words() + (" <abuse>" if rng.random() < 0.6 else "")
            add(h, txt, p["ts"] + rng.lognormvariate(7.8, 1.2), p["post_id"])

    return posts, reposts, truth


def to_batches(posts, reposts, n_clients=3, batch_size=400, seed=7):
    rng = random.Random(seed)
    clients = [uuid.UUID(int=rng.getrandbits(128)).hex for _ in range(n_clients)]
    out = []
    ps = [{"post_id": p["post_id"], "author": p["author"], "text": p["text"],
           "created_at": datetime.fromtimestamp(p["ts"], timezone.utc).isoformat(),
           "parent_id": p["parent_id"]} for p in posts]
    for i in range(0, len(ps), batch_size):
        out.append({"client_id": rng.choice(clients), "platform": "x", "posts": ps[i:i + batch_size], "reposts": []})
    for i in range(0, len(reposts), batch_size):
        out.append({"client_id": rng.choice(clients), "platform": "x", "posts": [],
                    "reposts": reposts[i:i + batch_size]})
    return out


def evaluate(scores: dict[str, dict], truth: dict[str, str]) -> dict:
    """Per ground-truth group: how many members got each label kind."""
    rows = {}
    for grp in sorted(set(truth.values())):
        members = [h for h, g in truth.items() if g == grp]
        counts = defaultdict(int)
        for h in members:
            for lab in scores.get(h.lower(), {}).get("labels", []):
                counts[lab["kind"]] += 1
        rows[grp] = {"members": len(members), "expected": EXPECTED[grp], **counts}
    return rows
