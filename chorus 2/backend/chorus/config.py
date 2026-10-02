"""Runtime settings. Every field can be overridden with an env var CHORUS_<FIELD_NAME_UPPER>."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields


@dataclass
class Settings:
    # Storage
    db_path: str = "chorus.db"
    retention_days: int = 30

    # Classifier: "auto" (baseline if trained model exists, else none), "baseline", "transformers", "none"
    classifier: str = "auto"
    baseline_model_path: str = "models/baseline.joblib"
    # Comma-separated "model_name:POSITIVE_LABEL" pairs for the transformers backend
    transformers_models: str = (
        "cardiffnlp/twitter-roberta-base-hate-latest:HATE,"
        "Hate-speech-CNERG/indic-abusive-allInOne-MuRIL:LABEL_1"
    )
    # Per-post cutoff. Baseline at 0.8: ~0.94 precision, ~0.67 recall (models/baseline_metrics.json)
    abuse_post_threshold: float = 0.8

    # Coordination detection
    sync_window_s: int = 120          # two replies to the same post this close in time count as synchronized
    min_shared_posts: int = 3         # a pair needs this many shared posts before it becomes a graph edge
    min_cluster_size: int = 3
    repost_pair_cap: int = 300        # skip co-repost pairing for posts with more reposters than this
    label_threshold: float = 0.5      # coordination score needed to show a label
    high_threshold: float = 0.75

    # Near-duplicate text
    dup_jaccard: float = 0.55
    min_text_len: int = 20

    # Copy-paste talking points
    narrative_window_h: float = 6.0
    copypasta_min_posts: int = 3
    copypasta_min_rate: float = 0.3

    # Abusive-content rate
    abuse_min_posts: int = 5
    abuse_min_flagged: int = 3
    abuse_rate_label: float = 0.4

    # Anti-poisoning: a post only counts once this many distinct clients reported it
    min_observers: int = 1

    # Ops
    analyze_interval_s: int = 60      # 0 disables the background loop
    admin_token: str = ""


def load_settings(**overrides) -> Settings:
    s = Settings()
    for f in fields(Settings):
        env = os.environ.get("CHORUS_" + f.name.upper())
        if env is not None:
            setattr(s, f.name, type(getattr(s, f.name))(env))
    for k, v in overrides.items():
        if not hasattr(s, k):
            raise AttributeError(f"unknown setting {k}")
        setattr(s, k, v)
    return s
