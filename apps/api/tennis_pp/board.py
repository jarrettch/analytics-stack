"""Validation for an uploaded board. The payload is untrusted: it comes from a third-party page
by way of the clipboard."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class ManualLine(BaseModel):
    """Book lines typed in for one match the odds feed doesn't carry."""

    model_config = ConfigDict(extra="forbid")

    game_id: str = Field(pattern=_ID)
    # One of the two players in that game on the board (checked in resolve); the moneylines and
    # aces are read from this player's side.
    player: str = Field(min_length=1, max_length=80)
    tour: Literal["atp", "wta"]
    ml_player: float = Field(allow_inf_nan=False)
    ml_opponent: float = Field(allow_inf_nan=False)
    # Under 30: a higher total reads as best of five, which the model doesn't price.
    total_line: float | None = Field(default=None, ge=15, lt=30, multiple_of=0.5, allow_inf_nan=False)
    total_over: float | None = Field(default=None, allow_inf_nan=False)
    total_under: float | None = Field(default=None, allow_inf_nan=False)
    # Expected aces for the match.
    aces_player: float | None = Field(default=None, ge=0, le=40, allow_inf_nan=False)
    aces_opponent: float | None = Field(default=None, ge=0, le=40, allow_inf_nan=False)

    @field_validator("ml_player", "ml_opponent", "total_over", "total_under")
    @classmethod
    def _american(cls, value: float | None) -> float | None:
        if value is not None and not 100 <= abs(value) <= 10000:
            raise ValueError("American odds run from -10000 to -100 and +100 to +10000")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "ManualLine":
        if min(self.ml_player, self.ml_opponent) > 120:
            raise ValueError("both moneylines are underdog prices; check the signs")
        if (self.total_over is None) != (self.total_under is None):
            raise ValueError("give both total prices or neither")
        if self.total_over is not None and self.total_line is None:
            raise ValueError("total prices need a total line")
        return self


class ManualLinesRequest(BaseModel):
    """The whole set for a run: a game left out goes back to the odds feed."""

    model_config = ConfigDict(extra="forbid")

    lines: list[ManualLine] = Field(max_length=60)

    def resolve(self, board: list[dict]) -> list[dict]:
        """Lines as stored and priced, with each opponent read from the board. Raises ValueError
        for a game or player the board doesn't have, or a game given twice."""
        # A player can be on the board only as someone's opponent.
        pairs = {}
        for row in board:
            pairs[(row["game_id"], row["player"])] = row["opponent"]
            pairs.setdefault((row["game_id"], row["opponent"]), row["player"])
        out, seen = [], set()
        for line in self.lines:
            opponent = pairs.get((line.game_id, line.player))
            if opponent is None:
                raise ValueError(f"{line.player} isn't in game {line.game_id} on this board")
            if line.game_id in seen:
                raise ValueError(f"game {line.game_id} has two sets of lines")
            seen.add(line.game_id)
            out.append({**line.model_dump(), "opponent": opponent})
        return out


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
