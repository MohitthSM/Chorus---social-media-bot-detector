import os

import pytest

from chorus.classifier import BaselineClassifier, TransformersClassifier, dominant_script, load_classifier
from chorus.config import load_settings

MODEL = os.path.join(os.path.dirname(__file__), "..", "models", "baseline.joblib")


def test_auto_without_model_disables_abuse_labels(tmp_path):
    assert load_classifier(load_settings(baseline_model_path=str(tmp_path / "missing.joblib"))) is None


@pytest.mark.skipif(not os.path.exists(MODEL), reason="run scripts/train_baseline.py first")
def test_baseline_separates_obvious_cases():
    clf = BaselineClassifier(MODEL)
    benign = ["Muslims in my neighbourhood organised a food drive for everyone",
              "modi ji ne bahut accha kaam kiya hai",
              "The government has failed farmers, this policy is a disaster"]
    p = clf.predict(benign)
    assert all(x < 0.8 for x in p), p


def test_transformers_backend_takes_max_of_positive_label(monkeypatch):
    """No model download: patch the HF pipeline factory."""
    import sys
    import types

    def fake_pipeline(task, model, **kw):
        def run(texts, batch_size=32):
            if model == "en":
                return [[{"label": "HATE", "score": 0.9 if "bad" in t else 0.1},
                         {"label": "NOT-HATE", "score": 0.1}] for t in texts]
            return [[{"label": "LABEL_1", "score": 0.7}, {"label": "LABEL_0", "score": 0.3}] for _ in texts]
        return run

    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(pipeline=fake_pipeline))
    clf = TransformersClassifier("en:HATE,indic:LABEL_1")
    assert clf.predict(["bad words", "fine"]) == [0.9, 0.7]


def test_dominant_script():
    assert dominant_script("hello world") == "LATIN"
    assert dominant_script("किसान भाई hello") == "DEVANAGARI"
    assert dominant_script("আমার সোনার বাংলা") == "BENGALI"
    assert dominant_script("123 !!") is None


@pytest.mark.skipif(not os.path.exists(MODEL), reason="run scripts/train_baseline.py first")
def test_baseline_skips_unsupported_scripts():
    clf = BaselineClassifier(MODEL)
    p = clf.predict(["আমার সোনার বাংলা আমি তোমায় ভালোবাসি", "have a nice day", "یہ ایک جملہ ہے"])
    assert p[0] is None and p[2] is None and isinstance(p[1], float)
