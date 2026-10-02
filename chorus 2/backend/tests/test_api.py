from chorus.simulate import generate, to_batches
from conftest import all_scores, make_client


def post(pid, author, text="hello there general kenobi", parent=None, ts="2026-09-01T10:00:00Z"):
    return {"post_id": pid, "author": author, "text": text, "created_at": ts, "parent_id": parent}


def test_health_and_stats():
    c = make_client()
    assert c.get("/health").json()["classifier"] == "keyword-stub"
    assert c.get("/v1/stats").json()["posts"] == 0


def test_validation_rejects_bad_input():
    c = make_client()
    base = {"client_id": "client-0001", "platform": "x"}
    assert c.post("/v1/observations", json={**base, "posts": [post("1", "bad handle!")]}).status_code == 422
    assert c.post("/v1/observations", json={**base, "platform": "myspace", "posts": []}).status_code == 422
    assert c.post("/v1/observations", json={**base, "client_id": "x", "posts": []}).status_code == 422
    too_many = [post(str(i), "a") for i in range(501)]
    assert c.post("/v1/observations", json={**base, "posts": too_many}).status_code == 422


def test_ingest_is_idempotent_and_lowercases():
    c = make_client()
    b = {"client_id": "client-0001", "platform": "x", "posts": [post("1", "SomeUser")]}
    c.post("/v1/observations", json=b)
    c.post("/v1/observations", json=b)
    assert c.get("/v1/stats").json()["posts"] == 1
    c.post("/v1/analyze")
    assert c.post("/v1/scores", json={"platform": "x", "handles": ["SOMEUSER"]}).json()["scores"]["someuser"]


def test_unknown_and_insufficient_data_status():
    c = make_client()
    c.post("/v1/observations", json={"client_id": "client-0001", "platform": "x",
                                     "posts": [post("1", "quiet")]})
    c.post("/v1/analyze")
    s = all_scores(c, ["quiet", "never_seen"])
    assert s["never_seen"]["status"] == "unknown"
    assert s["quiet"]["status"] in ("unknown", "insufficient_data")
    assert s["quiet"]["labels"] == []


def test_suppression_override_requires_admin_and_hides_labels():
    c = make_client(admin_token="secret")
    posts, reposts, truth = generate(seed=3)
    for b in to_batches(posts, reposts):
        c.post("/v1/observations", json=b)
    c.post("/v1/analyze")
    ring = next(h for h, g in truth.items() if g == "ring_a")
    assert all_scores(c, [ring])[ring]["status"] == "labeled"

    appeal = c.post("/v1/appeals", json={"platform": "x", "handle": ring, "message": "I am a real person"})
    assert appeal.status_code == 200
    assert c.get("/v1/admin/appeals").status_code == 403
    assert len(c.get("/v1/admin/appeals", headers={"X-Admin-Token": "secret"}).json()["appeals"]) == 1

    body = {"platform": "x", "handle": ring, "action": "suppress", "note": "verified human"}
    assert c.post("/v1/admin/overrides", json=body).status_code == 403
    assert c.post("/v1/admin/overrides", json=body, headers={"X-Admin-Token": "wrong"}).status_code == 403
    assert c.post("/v1/admin/overrides", json=body, headers={"X-Admin-Token": "secret"}).status_code == 200
    s = all_scores(c, [ring])[ring]
    assert s["status"] == "suppressed" and s["labels"] == []
    assert c.get("/v1/admin/appeals", headers={"X-Admin-Token": "secret"}).json()["appeals"] == []


def test_admin_disabled_without_token():
    c = make_client()
    assert c.get("/v1/admin/appeals", headers={"X-Admin-Token": ""}).status_code == 403


def test_min_observers_blocks_single_client_poisoning():
    """With min_observers=2, a ring reported by only one client produces no labels."""
    c = make_client(min_observers=2)
    posts, reposts, truth = generate(seed=5)
    for b in to_batches(posts, reposts, n_clients=1):
        c.post("/v1/observations", json=b)
    c.post("/v1/analyze")
    scores = all_scores(c, truth)
    assert not any(s["labels"] for s in scores.values())

    # a second, independent client seeing the same posts makes them count
    for b in to_batches(posts, reposts, n_clients=1, seed=99):
        c.post("/v1/observations", json=b)
    c.post("/v1/analyze")
    scores = all_scores(c, truth)
    assert any(s["labels"] for s in scores.values())


def test_future_timestamps_are_dropped():
    c = make_client()
    c.post("/v1/observations", json={"client_id": "client-0001", "platform": "x",
                                     "posts": [post("1", "a", ts="2099-01-01T00:00:00Z")]})
    row = c.app.state.store.load_posts("x", 1, 0)
    assert row[0]["created_at"] is None
