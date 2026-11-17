from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ModelConfig:
    """Every tunable a run uses. Stored with the run so it can be repriced exactly."""

    # Per service game, used when no book or PrizePicks ace line exists.
    ace_default: dict[str, float] = field(default_factory=lambda: {"atp": 0.45, "wta": 0.22})
    df_default: dict[str, float] = field(default_factory=lambda: {"atp": 0.20, "wta": 0.30})
    # Ace dispersion: var = mean + mean^2 / ace_k.
    ace_k: float = 5.0
    # Assumed overround on over-only ace ladders.
    one_sided_margin: float = 1.045
    # Actual FS minus the model, by tour (measure_fs_bias.py, 11-09). A lean is repriced with the
    # line moved against it only where the bias hurts that lean: WTA unders, ATP overs.
    bias_shift: dict[str, float] = field(default_factory=lambda: {"wta": 2.0, "atp": -2.0})
    mix_c: dict[str, float] = field(default_factory=lambda: {"atp": 0.64, "wta": 0.56})
    ml_only_s: dict[str, float] = field(default_factory=lambda: {"atp": 0.06, "wta": 0.07})
    # No best-of-3 match total sits under this; DK mixes a set-sized market into total_games.
    min_total_line: float = 15.0
    # A DK main total this high means best of five.
    best_of_five_total: float = 30.0
    blowout_ml: int = -400
    juice_ml: int = -135
    # Fit acceptance tolerances.
    fit_win_tol: float = 0.01
    fit_over_tol: float = 0.02
    # How far a book event's start may sit from PrizePicks' start time. Both names must match
    # as well, so this only has to separate two meetings of the same pair. The spec's 12 h was
    # too tight: matches pushed to the next day sat 20 h apart on the 11-15 board, where
    # PrizePicks had the new time and the book the old one.
    match_window_hours: float = 36.0

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT_CONFIG = ModelConfig()
