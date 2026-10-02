"""Train and evaluate the baseline abusive-content classifier.

Data (downloaded with git on first run into data/raw/):
  - HateXplain (English; Twitter + Gab; MIT licence) https://github.com/hate-alert/HateXplain
      label 1 = majority annotator label "hatespeech" or "offensive", 0 = "normal"
  - MACD (Hindi, Tamil, Telugu, Malayalam, Kannada; ShareChat) https://github.com/ShareChatAI/MACD
      label 1 = abusive. No licence file in the repo: treat as research-only, do not redistribute.
  - TRAC-1 Hindi (romanised Hinglish + Devanagari Facebook comments; Apache-2.0)
      https://github.com/kmi-linguistics/trac-1
      label 1 = overtly aggressive (OAG), 0 = non-aggressive (NAG); covertly aggressive (CAG) dropped.
      Without this, romanised Hinglish is out of distribution and benign Hinglish gets flagged.

Model: TF-IDF word 1-2grams + char_wb 2-5grams -> logistic regression. Fast, CPU only, and
script-agnostic. It is a baseline, not a production model; see README for the transformer option.

Usage:
  python scripts/train_baseline.py                  # train on both, write models/baseline.joblib
  python scripts/train_baseline.py --datasets hatexplain
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score
from sklearn.pipeline import FeatureUnion, Pipeline

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
REPOS = {"hatexplain": "https://github.com/hate-alert/HateXplain.git",
         "macd": "https://github.com/ShareChatAI/MACD.git",
         "trac": "https://github.com/kmi-linguistics/trac-1.git"}
MACD_LANGS = ["hindi", "tamil", "telugu", "malyalam", "kannada"]


def fetch(name: str) -> Path:
    dest = RAW / name
    if not dest.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        print(f"cloning {REPOS[name]} ...")
        subprocess.run(["git", "clone", "--depth", "1", REPOS[name], str(dest)], check=True)
    return dest


def load_hatexplain() -> dict[str, pd.DataFrame]:
    d = fetch("hatexplain") / "Data"
    data = json.loads((d / "dataset.json").read_text())
    splits = json.loads((d / "post_id_divisions.json").read_text())
    out = {}
    for split, key in (("train", "train"), ("val", "val"), ("test", "test")):
        rows = []
        for pid in splits[key]:
            item = data[pid]
            label = Counter(a["label"] for a in item["annotators"]).most_common(1)[0][0]
            rows.append({"text": " ".join(item["post_tokens"]), "label": int(label != "normal")})
        out[split] = pd.DataFrame(rows).assign(source="hatexplain")
    return out


def load_macd() -> dict[str, pd.DataFrame]:
    d = fetch("macd") / "dataset"
    out = {}
    for split in ("train", "val", "test"):
        frames = []
        for lang in MACD_LANGS:
            df = pd.read_csv(d / f"{lang}_{split}.csv")
            # MACD: 0 = abusive, 1 = non-abusive
            frames.append(pd.DataFrame({"text": df["text"].astype(str), "label": (df["label"] == 0).astype(int),
                                        "source": f"macd-{lang}"}))
        out[split] = pd.concat(frames, ignore_index=True)
    return out


def load_trac() -> dict[str, pd.DataFrame]:
    d = fetch("trac") / "hindi"
    out = {}
    train = pd.read_csv(d / "agr_hi_train.csv", header=None, names=["id", "text", "agr"])
    test = pd.read_csv(d / "agr_hi_dev.csv", header=None, names=["id", "text", "agr"])
    for split, df in (("train", train), ("test", test)):
        df = df[df["agr"].isin(["OAG", "NAG"])].dropna(subset=["text"])
        out[split] = pd.DataFrame({"text": df["text"].astype(str), "label": (df["agr"] == "OAG").astype(int),
                                   "source": "trac-hindi"})
    out["val"] = out["train"].iloc[:0]
    return out


def build_model() -> Pipeline:
    feats = FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=200_000, sublinear_tf=True)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=3, max_features=300_000,
                                 sublinear_tf=True)),
    ])
    return Pipeline([("feats", feats),
                     ("lr", LogisticRegression(C=4.0, max_iter=3000, class_weight="balanced"))])


def metrics(y, p, thr=0.5) -> dict:
    yhat = (p >= thr).astype(int)
    return {"n": len(y), "positive_rate": round(float(np.mean(y)), 3),
            "precision": round(precision_score(y, yhat, zero_division=0), 3),
            "recall": round(recall_score(y, yhat, zero_division=0), 3),
            "f1": round(f1_score(y, yhat, zero_division=0), 3),
            "macro_f1": round(f1_score(y, yhat, average="macro", zero_division=0), 3),
            "avg_precision": round(average_precision_score(y, p), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", default="hatexplain,macd,trac")
    ap.add_argument("--out", default=str(ROOT / "models" / "baseline.joblib"))
    a = ap.parse_args()

    loaders = {"hatexplain": load_hatexplain, "macd": load_macd, "trac": load_trac}
    parts = [loaders[n.strip()]() for n in a.datasets.split(",")]
    train = pd.concat([p["train"] for p in parts] + [p["val"] for p in parts], ignore_index=True)
    test = pd.concat([p["test"] for p in parts], ignore_index=True)
    train, test = (df[df["text"].notna()].reset_index(drop=True) for df in (train, test))
    print(f"train {len(train)}  test {len(test)}")

    model = build_model().fit(train["text"], train["label"])
    p = model.predict_proba(test["text"])[:, 1]

    report = {}
    for thr in (0.5, 0.8):
        report[f"threshold_{thr}"] = r = {"overall": metrics(test["label"].values, p, thr)}
        for src, idx in test.groupby("source").groups.items():
            r[src] = metrics(test.loc[idx, "label"].values, p[test.index.get_indexer(idx)], thr)
        print(f"--- threshold {thr}")
        for k, v in r.items():
            print(f"{k:18s} " + "  ".join(f"{m}={x}" for m, x in v.items()))

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out, compress=3)
    (out.parent / "baseline_metrics.json").write_text(json.dumps(report, indent=2))
    print(f"saved {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
