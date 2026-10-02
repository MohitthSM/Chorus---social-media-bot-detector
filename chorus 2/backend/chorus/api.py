"""HTTP API used by the browser extension."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from .analysis import CAVEAT, run_analysis
from .classifier import Classifier, load_classifier
from .config import Settings, load_settings
from .db import Store

log = logging.getLogger("chorus.api")

Platform = Literal["x", "instagram", "facebook"]
HANDLE = r"^[A-Za-z0-9_.]{1,30}$"
POST_ID = r"^[A-Za-z0-9_-]{1,64}$"


class PostIn(BaseModel):
    post_id: str = Field(pattern=POST_ID)
    author: str = Field(pattern=HANDLE)
    display_name: str | None = Field(default=None, max_length=100)
    text: str = Field(default="", max_length=5000)
    created_at: datetime | None = None
    parent_id: str | None = Field(default=None, pattern=POST_ID)
    metrics: dict[str, int] | None = None

    @field_validator("author")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class RepostIn(BaseModel):
    actor: str = Field(pattern=HANDLE)
    post_id: str = Field(pattern=POST_ID)

    @field_validator("actor")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class ObservationBatch(BaseModel):
    client_id: str = Field(min_length=8, max_length=64)
    platform: Platform
    posts: list[PostIn] = Field(default_factory=list, max_length=500)
    reposts: list[RepostIn] = Field(default_factory=list, max_length=500)


class ScoresRequest(BaseModel):
    platform: Platform
    handles: list[str] = Field(max_length=200)


class AppealIn(BaseModel):
    platform: Platform
    handle: str = Field(pattern=HANDLE)
    message: str = Field(min_length=1, max_length=2000)
    contact: str | None = Field(default=None, max_length=200)


class OverrideIn(BaseModel):
    platform: Platform
    handle: str = Field(pattern=HANDLE)
    action: Literal["suppress", "clear"]
    note: str | None = Field(default=None, max_length=500)


def create_app(settings: Settings | None = None, classifier: Classifier | None | str = "load") -> FastAPI:
    s = settings or load_settings()
    store = Store(s.db_path)
    clf = load_classifier(s) if classifier == "load" else classifier
    state = {"last_analysis": None}

    def analyze() -> dict:
        res = run_analysis(store, s, clf)
        state["last_analysis"] = {"at": time.time(), **res}
        return res

    async def loop():
        while True:
            await asyncio.sleep(s.analyze_interval_s)
            try:
                await asyncio.to_thread(analyze)
            except Exception:  # keep the loop alive
                log.exception("analysis failed")

    @asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(loop()) if s.analyze_interval_s > 0 else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Chorus", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"^(chrome-extension|moz-extension)://.*$",
                       allow_methods=["GET", "POST"], allow_headers=["*"])

    def require_admin(token: str | None):
        if not s.admin_token or token != s.admin_token:
            raise HTTPException(403, "admin token required")

    @app.get("/health")
    def health():
        return {"ok": True, "classifier": clf.name if clf else None, "last_analysis": state["last_analysis"]}

    @app.get("/v1/stats")
    def stats():
        return store.stats()

    @app.post("/v1/observations")
    def observations(batch: ObservationBatch):
        now = time.time()
        posts = []
        for p in batch.posts:
            d = p.model_dump()
            ts = p.created_at.timestamp() if p.created_at else None
            if ts is not None and ts > now + 86400:
                ts = None
            d["created_at"] = ts
            d["text"] = p.text.strip()
            posts.append(d)
        return store.ingest(batch.platform, batch.client_id, posts, [r.model_dump() for r in batch.reposts])

    @app.post("/v1/scores")
    def scores(req: ScoresRequest):
        handles = sorted({h.lower() for h in req.handles if h})
        found = store.get_scores(req.platform, handles)
        out = {}
        for h in handles:
            sc = found.get(h, {"computed_at": None, "status": "unknown", "labels": [], "evidence": {}})
            out[h] = sc | {"caveat": CAVEAT}
        return {"scores": out}

    @app.get("/v1/accounts/{platform}/{handle}")
    def account(platform: Platform, handle: str):
        h = handle.lower()
        sc = store.get_scores(platform, [h]).get(h)
        if not sc:
            raise HTTPException(404, "no data for this account")
        return sc | {"handle": h, "caveat": CAVEAT}

    @app.get("/v1/clusters/{platform}")
    def clusters(platform: Platform):
        return {"clusters": store.get_clusters(platform)}

    @app.post("/v1/analyze")
    def analyze_now():
        return analyze()

    @app.post("/v1/appeals")
    def appeal(a: AppealIn):
        return {"id": store.add_appeal(a.platform, a.handle.lower(), a.message, a.contact)}

    @app.get("/v1/admin/appeals")
    def list_appeals(x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        return {"appeals": store.list_appeals()}

    @app.post("/v1/admin/overrides")
    def override(o: OverrideIn, x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        store.set_override(o.platform, o.handle.lower(), o.action, o.note)
        return {"ok": True}

    app.state.store = store
    app.state.settings = s
    return app


def main():
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="Run the Chorus backend")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host=a.host, port=a.port)


if __name__ == "__main__":
    main()
