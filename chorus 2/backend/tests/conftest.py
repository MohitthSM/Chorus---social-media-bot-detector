import pytest
from fastapi.testclient import TestClient

from chorus.api import create_app
from chorus.config import load_settings
from chorus.simulate import KeywordClassifier, generate, to_batches


def make_client(**overrides):
    s = load_settings(db_path=":memory:", analyze_interval_s=0, **overrides)
    return TestClient(create_app(s, classifier=KeywordClassifier()))


def all_scores(client, handles):
    out = {}
    handles = list(handles)
    for i in range(0, len(handles), 200):
        r = client.post("/v1/scores", json={"platform": "x", "handles": handles[i:i + 200]})
        assert r.status_code == 200, r.text
        out.update(r.json()["scores"])
    return out


@pytest.fixture(scope="module")
def simulated():
    """Simulated network ingested through the API and analyzed once."""
    client = make_client()
    posts, reposts, truth = generate(seed=7)
    for b in to_batches(posts, reposts):
        assert client.post("/v1/observations", json=b).status_code == 200
    summary = client.post("/v1/analyze").json()
    return client, truth, all_scores(client, truth), summary
