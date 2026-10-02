"""Abusive/hateful text classifiers. Each returns P(abusive) in [0, 1] per text, or None if unsupported."""
from __future__ import annotations

import logging
import os
import unicodedata
from typing import Protocol

from .config import Settings

log = logging.getLogger("chorus.classifier")


class Classifier(Protocol):
    name: str

    def predict(self, texts: list[str]) -> list[float | None]:
        """P(abusive) per text, or None where the model does not support the language."""
        ...


def dominant_script(text: str) -> str | None:
    """Most common Unicode script among letters, e.g. LATIN, DEVANAGARI, BENGALI, ARABIC."""
    counts: dict[str, int] = {}
    for ch in text:
        if ch.isalpha():
            try:
                sc = unicodedata.name(ch).split(" ")[0]
            except ValueError:
                continue
            counts[sc] = counts.get(sc, 0) + 1
    return max(counts, key=counts.get) if counts else None


class BaselineClassifier:
    """TF-IDF (word + char n-grams) + logistic regression. Trained by scripts/train_baseline.py.

    Only scores scripts it was trained on. Text in other scripts (Bengali, Urdu/Arabic, ...) gets
    None instead of a score, because out-of-distribution text produces confident false positives.
    """

    name = "baseline"
    SCRIPTS = frozenset({"LATIN", "DEVANAGARI", "TAMIL", "TELUGU", "KANNADA", "MALAYALAM"})

    def __init__(self, path: str):
        import joblib
        self.model = joblib.load(path)

    def predict(self, texts: list[str]) -> list[float | None]:
        idx = [i for i, t in enumerate(texts) if dominant_script(t) in self.SCRIPTS]
        out: list[float | None] = [None] * len(texts)
        if idx:
            probs = self.model.predict_proba([texts[i] for i in idx])[:, 1]
            for i, p in zip(idx, probs, strict=True):
                out[i] = float(p)
        return out


class TransformersClassifier:
    """Runs one or more Hugging Face text-classification models and takes the max P(abusive).

    `spec` is "model:POSITIVE_LABEL,model2:POSITIVE_LABEL". Check each model card for which
    label means abusive; it differs between models.
    """

    name = "transformers"

    def __init__(self, spec: str):
        from transformers import pipeline
        self.models = []
        for part in filter(None, (p.strip() for p in spec.split(","))):
            model, _, pos = part.rpartition(":")
            self.models.append((pipeline("text-classification", model=model, top_k=None, truncation=True), pos))

    def predict(self, texts: list[str]) -> list[float]:
        best = [0.0] * len(texts)
        for pipe, pos in self.models:
            for i, scores in enumerate(pipe(texts, batch_size=32)):
                p = next((d["score"] for d in scores if d["label"] == pos), 0.0)
                best[i] = max(best[i], p)
        return best


def load_classifier(s: Settings) -> Classifier | None:
    kind = s.classifier
    if kind == "auto":
        kind = "baseline" if os.path.exists(s.baseline_model_path) else "none"
    if kind == "none":
        log.warning("No abuse classifier loaded; abusive-content labels are disabled. "
                    "Run scripts/train_baseline.py or set CHORUS_CLASSIFIER=transformers.")
        return None
    if kind == "baseline":
        return BaselineClassifier(s.baseline_model_path)
    if kind == "transformers":
        return TransformersClassifier(s.transformers_models)
    raise ValueError(f"unknown classifier {kind!r}")
