"""Validation for an uploaded board. The payload is untrusted: it comes from a third-party page
by way of the clipboard."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_BODY_BYTES = 256 * 1024
# WTA Finals lines were posted 3+ days out.
MAX_START_OFFSET = timedelta(days=4)
_ID = r"^\d{1,20}$"


class BoardRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player: str = Field(min_length=1, max_length=80)
    opponent: str = Field(min_length=1, max_length=80)
    stat: Literal["Fantasy Score", "Total Games", "Total Games Won", "Aces"]
    odds_type: Literal["standard", "goblin", "demon"]
    line: float = Field(ge=0, le=60, allow_inf_nan=False)
    start_time: datetime
    status: Literal["pre_game"]
    projection_id: str = Field(pattern=_ID)
    game_id: str = Field(pattern=_ID)

    @field_validator("player", "opponent")
    @classmethod
    def _name(cls, value: str) -> str:
        # Names are matched and displayed, never interpolated. Doubles ("A / B") fail here.
        if not all(ch.isalpha() or ch in " .'-" for ch in value):
            raise ValueError("names may contain only letters, spaces, '.', ''' and '-'")
        return value

    @field_validator("start_time")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("start_time needs a UTC offset")
        return value


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    board_saved_at: datetime
    rows: list[BoardRow] = Field(min_length=1, max_length=1000)

    def board(self, now: datetime) -> list[dict]:
        """Rows as stored and priced. Raises ValueError for a start time too far from now."""
        out = []
        for row in self.rows:
            if abs(row.start_time - now) > MAX_START_OFFSET:
                raise ValueError(f"start_time for {row.player} is more than 4 days from now")
            out.append({**row.model_dump(exclude={"start_time"}), "start_time": row.start_time.isoformat()})
        return out
