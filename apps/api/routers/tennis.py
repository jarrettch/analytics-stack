"""PrizePicks tennis board runs. See specs/tennis_fs_upload.md.

Nothing here fetches PrizePicks: the board comes from the user's own browser.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from tennis_pp.board import MAX_BODY_BYTES, ManualLinesRequest, RunRequest
from tennis_pp.runs import TennisRuns
from tennis_pp.store import SupabaseStore

from . import utils
from .auth import require_prep_auth
from .sharp_odds import SharpSweep, fetch_sharp_odds

router = APIRouter(prefix="/api/tennis", tags=["Tennis"], dependencies=[Depends(require_prep_auth)])

_service: TennisRuns | None = None


async def _fetch(league: str, market: str, *, main_only: bool) -> list[dict]:
    return await fetch_sharp_odds(league, market, sweep=SharpSweep(), main_only=main_only)


def get_service() -> TennisRuns:
    """One per process: it owns the running refresh task and the fit cache."""
    global _service
    if _service is None:
        if utils.supabase is None:
            raise HTTPException(status_code=503, detail="Run storage is not configured")
        _service = TennisRuns(SupabaseStore(utils.supabase), _fetch)
    return _service


def _found(view: dict | None) -> dict:
    if view is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return view


@router.post("/runs")
async def create_run(request: Request, service: TennisRuns = Depends(get_service)):
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Board is larger than 256 KB")
    try:
        payload = RunRequest.model_validate_json(body)
        board = payload.board(datetime.now(timezone.utc))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False, include_input=False, include_context=False)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await service.create(board, payload.board_saved_at)


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, service: TennisRuns = Depends(get_service)):
    """The run as stored. It never reprices: an old run shows what it showed then."""
    return _found(service.view(str(run_id)))


@router.put("/runs/{run_id}/manual-lines")
async def set_manual_lines(run_id: UUID, payload: ManualLinesRequest, service: TennisRuns = Depends(get_service)):
    """Book lines typed in for matches the feed doesn't carry. Sends the whole set each time and
    answers with the repriced run, a new revision of this one."""
    run = service.store.run(str(run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        lines = payload.resolve(run["board"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return _found(await service.set_manual_lines(str(run_id), lines))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/refresh")
async def retry_refresh(run_id: UUID, service: TennisRuns = Depends(get_service)):
    return _found(service.retry(str(run_id)))
