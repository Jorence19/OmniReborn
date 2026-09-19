"""Read-only Method B API for the Hostinger dashboard."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

APP_DIR = Path(__file__).resolve().parent
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
CONFIDENCE = {"HIGH_LEAD", "PROBABLE_LEAD", "WATCH", "WEAK"}
MAX_SNAPSHOT_BYTES = 25 * 1024 * 1024
MAX_CANDIDATES = 20_000
MAX_EVIDENCE_PER_CANDIDATE = 50


def _csv_env(name: str, default: str = "") -> list[str]:
    return [part.strip() for part in os.getenv(name, default).split(",") if part.strip()]


def _finite_number(value: Any, default: float = 0.0, minimum: float | None = None,
                   maximum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if not math.isfinite(result):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    return str(value).replace("\x00", "").strip()[:limit]


def _address(value: Any, *, required: bool = False) -> str:
    result = _text(value, 42)
    if ADDRESS_RE.fullmatch(result):
        return result
    if required:
        raise ValueError("candidate has an invalid contract address")
    return ""


def sanitize_candidate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("candidate must be an object")
    confidence = _text(raw.get("confidence"), 32).upper()
    if confidence not in CONFIDENCE:
        confidence = "WEAK"
    evidence: list[dict[str, Any]] = []
    raw_evidence = raw.get("evidence")
    if isinstance(raw_evidence, list):
        for item in raw_evidence[:MAX_EVIDENCE_PER_CANDIDATE]:
            if not isinstance(item, dict):
                continue
            ev_type = _text(item.get("type"), 24).lower()
            if ev_type not in {"numeric", "categorical", "feature"}:
                ev_type = "feature"
            evidence.append({
                "feature": _text(item.get("feature"), 80),
                "value": _text(item.get("value"), 500),
                "anchor_value": _text(item.get("anchor_value"), 500),
                "type": ev_type,
                "reliability": _finite_number(item.get("reliability"), 0.0, 0.0, 1.0),
                "proximity": _finite_number(item.get("proximity"), 1.0, 0.0, 1.0),
                "contribution": _finite_number(item.get("contribution"), 0.0, 0.0, 1.0),
            })
    ath = _finite_number(raw.get("ath"), 0.0, 0.0)
    try:
        chain_id = int(raw.get("chain_id", 4663))
    except (TypeError, ValueError):
        chain_id = 4663
    if chain_id not in {4663, 5042}:
        chain_id = 4663
    chain = "ARC" if chain_id == 5042 else "RBH"
    try:
        best_match_chain_id = int(raw.get("best_match_chain_id", chain_id))
    except (TypeError, ValueError):
        best_match_chain_id = chain_id
    if best_match_chain_id not in {4663, 5042}:
        best_match_chain_id = chain_id
    best_match_chain = "ARC" if best_match_chain_id == 5042 else "RBH"
    return {
        "ca": _address(raw.get("ca"), required=True),
        "chain_id": chain_id,
        "chain": chain,
        "symbol": _text(raw.get("symbol"), 80),
        "name": _text(raw.get("name"), 200),
        "confidence": confidence,
        "score": round(_finite_number(raw.get("score"), 0.0, 0.0, 100.0), 4),
        "team": _text(raw.get("team"), 120) or "unclustered",
        "best_match_symbol": _text(raw.get("best_match_symbol"), 80),
        "best_match_ca": _address(raw.get("best_match_ca")),
        "best_match_chain_id": best_match_chain_id,
        "best_match_chain": best_match_chain,
        "ath": ath,
        "is_rug": bool(raw.get("is_rug", ath <= 5000.0)),
        "evidence": evidence,
        "token_live": _text(raw.get("token_live"), 64),
        "website": _text(raw.get("website"), 500),
        "x": _text(raw.get("x"), 500),
    }


@dataclass(frozen=True)
class ApiSettings:
    snapshot_path: Path
    allowed_origins: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    max_snapshot_age_seconds: int

    @classmethod
    def from_env(cls) -> "ApiSettings":
        return cls(
            snapshot_path=Path(os.getenv(
                "API_SNAPSHOT_PATH",
                str(APP_DIR / "runtime" / "dashboard_candidates.json"),
            )).resolve(),
            allowed_origins=tuple(_csv_env("API_ALLOWED_ORIGINS")),
            allowed_hosts=tuple(_csv_env(
                "API_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
            )),
            max_snapshot_age_seconds=max(
                60, int(os.getenv("API_MAX_SNAPSHOT_AGE_SECONDS", "3600"))
            ),
        )


class SnapshotStore:
    def __init__(self, settings: ApiSettings):
        self.settings = settings
        self._lock = threading.Lock()
        self._mtime_ns = -1
        self._payload: dict[str, Any] | None = None
        self._etag = ""

    def read(self) -> tuple[dict[str, Any], str, float]:
        path = self.settings.snapshot_path
        try:
            stat = path.stat()
        except OSError as exc:
            raise RuntimeError("candidate snapshot is unavailable") from exc
        if stat.st_size <= 0 or stat.st_size > MAX_SNAPSHOT_BYTES:
            raise RuntimeError("candidate snapshot size is invalid")
        with self._lock:
            if self._payload is None or self._mtime_ns != stat.st_mtime_ns:
                try:
                    raw_bytes = path.read_bytes()
                    document = json.loads(raw_bytes)
                except (OSError, json.JSONDecodeError) as exc:
                    raise RuntimeError("candidate snapshot is unreadable") from exc
                if not isinstance(document, dict) or not isinstance(document.get("candidates"), list):
                    raise RuntimeError("candidate snapshot schema is invalid")
                if len(document["candidates"]) > MAX_CANDIDATES:
                    raise RuntimeError("candidate snapshot exceeds the public API limit")
                candidates = []
                for candidate in document["candidates"]:
                    try:
                        candidates.append(sanitize_candidate(candidate))
                    except ValueError:
                        continue
                generated_at = _text(document.get("generated_at"), 64)
                self._payload = {
                    "schema_version": 1,
                    "generated_at": generated_at,
                    "count": len(candidates),
                    "candidates": candidates,
                }
                self._etag = '"' + hashlib.sha256(raw_bytes).hexdigest() + '"'
                self._mtime_ns = stat.st_mtime_ns
            age = max(0.0, time.time() - stat.st_mtime)
            return self._payload, self._etag, age


def readiness_checks(settings: ApiSettings, store: SnapshotStore) -> list[str]:
    errors: list[str] = []
    if not settings.allowed_origins:
        errors.append("API_ALLOWED_ORIGINS is required for the Hostinger frontend")
    for origin in settings.allowed_origins:
        if origin == "*" or "yourdomain.com" in origin or not origin.startswith("https://") or origin.endswith("/"):
            errors.append(f"unsafe API_ALLOWED_ORIGINS entry: {origin!r}")
    if not settings.allowed_hosts:
        errors.append("API_ALLOWED_HOSTS is required")
    for host in settings.allowed_hosts:
        if host == "*" or "example.com" in host or "yourdomain.com" in host or "://" in host or "/" in host:
            errors.append(f"unsafe API_ALLOWED_HOSTS entry: {host!r}")
    try:
        payload, _, _ = store.read()
        if payload["count"] == 0:
            errors.append("candidate snapshot is empty")
    except RuntimeError as exc:
        errors.append(str(exc))
    return errors


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or ApiSettings.from_env()
    store = SnapshotStore(settings)
    application = FastAPI(
        title="OmniReborn Candidate API", version="1.0.0",
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    application.state.settings = settings
    application.state.snapshot_store = store
    application.add_middleware(
        TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts)
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Accept", "If-None-Match"],
        expose_headers=["ETag", "X-Snapshot-Age"],
        max_age=600,
    )

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    @application.get("/api/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/health")
    def health(response: Response) -> dict[str, Any]:
        try:
            payload, _, age = store.read()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        stale = age > settings.max_snapshot_age_seconds
        response.headers["Cache-Control"] = "no-store"
        return {
            "status": "stale" if stale else "ok",
            "snapshot_age_seconds": round(age, 1),
            "max_snapshot_age_seconds": settings.max_snapshot_age_seconds,
            "candidate_count": payload["count"],
        }

    @application.get("/api/candidates")
    def candidates(if_none_match: str | None = Header(default=None)) -> Response:
        try:
            payload, etag, age = store.read()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        headers = {
            "ETag": etag,
            "Cache-Control": "public, max-age=30, stale-if-error=300",
            "X-Snapshot-Age": str(round(age, 1)),
        }
        if if_none_match == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(payload, headers=headers)

    return application


app = create_app()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate Method B API configuration")
    args = parser.parse_args()
    if not args.check:
        parser.error("run this service with uvicorn, or use --check")
    settings = ApiSettings.from_env()
    errors = readiness_checks(settings, SnapshotStore(settings))
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(
        f"OK: snapshot={settings.snapshot_path} "
        f"origins={','.join(settings.allowed_origins)} "
        f"hosts={','.join(settings.allowed_hosts)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
