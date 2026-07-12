"""Opt-in authenticated operational metrics endpoints."""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import PlainTextResponse

from multiagent_framework.operational_metrics import (
    OperationalMetricsCollector,
    render_prometheus,
)

from . import _state as state

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _require_metrics_access(token: str | None) -> None:
    enabled = os.environ.get("WORLD_SIM_ENABLE_METRICS_API", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=404, detail="Not found")
    expected = os.environ.get("WORLD_SIM_METRICS_TOKEN", "")
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


def _snapshot(token: str | None) -> dict:
    _require_metrics_access(token)
    if state.orchestrator is None:
        raise HTTPException(status_code=503, detail="Simulation is not initialized")
    return OperationalMetricsCollector(state.orchestrator).collect()


@router.get("")
async def metrics_json(
    x_metrics_token: str | None = Header(default=None, alias="X-Metrics-Token"),
):
    return _snapshot(x_metrics_token)


@router.get("/prometheus", response_class=PlainTextResponse)
async def metrics_prometheus(
    x_metrics_token: str | None = Header(default=None, alias="X-Metrics-Token"),
):
    return PlainTextResponse(
        render_prometheus(_snapshot(x_metrics_token)),
        media_type="text/plain; version=0.0.4",
    )
