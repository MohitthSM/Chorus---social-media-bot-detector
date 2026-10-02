"""End-to-end detection quality on the synthetic network (see chorus/simulate.py)."""
from chorus.simulate import EXPECTED


def members(truth, group):
    return [h.lower() for h, g in truth.items() if g == group]


def kinds(scores, h):
    return {lab["kind"] for lab in scores[h]["labels"]}


def test_rings_are_labeled_coordinated(simulated):
    _, truth, scores, _ = simulated
    for group in ("ring_a", "ring_b"):
        hit = [h for h in members(truth, group) if "coordinated" in kinds(scores, h)]
        assert len(hit) >= 0.9 * len(members(truth, group)), group


def test_templated_ring_is_high_confidence(simulated):
    _, truth, scores, _ = simulated
    levels = [lab["level"] for h in members(truth, "ring_a") for lab in scores[h]["labels"]
              if lab["kind"] == "coordinated"]
    assert levels and all(lv == "high" for lv in levels)


def test_no_false_positives_on_hard_negatives(simulated):
    _, truth, scores, _ = simulated
    for group, expected in EXPECTED.items():
        if expected is None:
            flagged = [h for h in members(truth, group) if scores[h]["labels"]]
            if group == "ring_c":
                continue  # a missed ring is a false negative, checked separately
            assert flagged == [], f"{group}: {flagged[:5]}"


def test_fans_are_clustered_but_gated(simulated):
    """Fans of one account do form a cluster; the single-target gate is what keeps them unlabeled."""
    _, truth, scores, _ = simulated
    gated = [scores[h]["evidence"].get("coordination", {}).get("gate") for h in members(truth, "fan")]
    assert gated.count("single_target_without_duplicate_text") >= 10


def test_known_limitation_single_target_llm_ring_is_missed(simulated):
    """Documents current behaviour. If this starts failing because ring_c is detected, update the README."""
    _, truth, scores, _ = simulated
    assert not any("coordinated" in kinds(scores, h) for h in members(truth, "ring_c"))


def test_copypasta_campaign(simulated):
    _, truth, scores, _ = simulated
    assert all("copypasta" in kinds(scores, h) for h in members(truth, "campaign"))


def test_abusive_accounts(simulated):
    _, truth, scores, _ = simulated
    hit = [h for h in members(truth, "abusive") if "abusive" in kinds(scores, h)]
    assert len(hit) >= 5


def test_labels_carry_evidence_and_caveat(simulated):
    _, truth, scores, _ = simulated
    h = members(truth, "ring_a")[0]
    sc = scores[h]
    lab = next(lab for lab in sc["labels"] if lab["kind"] == "coordinated")
    assert "cluster" in lab["summary"]
    ex = sc["evidence"]["coordination"]["examples"]
    assert ex and ex[0]["url"].startswith("https://x.com/")
    assert "not a verdict" in sc["caveat"]


def test_clusters_endpoint(simulated):
    client, truth, _, _ = simulated
    clusters = client.get("/v1/clusters/x").json()["clusters"]
    ring_a = set(members(truth, "ring_a"))
    best = max(clusters, key=lambda c: len(ring_a & set(c["members"])))
    assert len(ring_a & set(best["members"])) >= 11
    assert best["stats"]["median_sync_gap_s"] is not None
