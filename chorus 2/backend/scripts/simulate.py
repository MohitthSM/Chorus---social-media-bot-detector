"""Push a synthetic network with known ground truth into a running backend and report detection.

    python -m chorus.api &                      # or: chorus-backend
    python scripts/simulate.py --url http://localhost:8000

Abusive posts in the simulation contain the token "<abuse>", which real classifiers do not know,
so the "abusive" row is only meaningful when the backend runs with the keyword stub (the tests do
that). Coordination and copy-paste rows are classifier-independent.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chorus.simulate import evaluate, generate, to_batches


def call(url, path, body=None):
    req = urllib.request.Request(url.rstrip("/") + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    posts, reposts, truth = generate(seed=a.seed)
    for b in to_batches(posts, reposts, seed=a.seed):
        call(a.url, "/v1/observations", b)
    print("analysis:", call(a.url, "/v1/analyze", {}))

    handles, scores = list(truth), {}
    for i in range(0, len(handles), 200):
        scores.update(call(a.url, "/v1/scores", {"platform": "x", "handles": handles[i:i + 200]})["scores"])

    print(f"\n{'group':10s} {'members':>7s}  {'expected':12s} labels given")
    for grp, row in evaluate(scores, truth).items():
        given = ", ".join(f"{k}={v}" for k, v in row.items() if k not in ("members", "expected")) or "-"
        print(f"{grp:10s} {row['members']:7d}  {row['expected']!s:12s} {given}")


if __name__ == "__main__":
    main()
