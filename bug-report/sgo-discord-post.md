Has anyone else hit this? MLB player props go missing for any player whose roster
situation changed after his entity was created — the player→team association
never gets updated, so he can't attach to an event and his props simply aren't in
the feed. Not unavailable odds. Absent.

Three variants, all via `GET /v2/players?playerID=<id>&leagueID=MLB`:

```
CLAY_HOLMES_1_MLB       teamID: NEW_YORK_METS_MLB   (a Cub since Aug 8)
QUINN_MATHEWS_1_MLB     no entity at all            (debuted this month)
ROBERT_STOCK_1_MLB      teamID: ""                  (no team)
```

Cleanest repro, event `JoOt8rGOFQFp4PSMu8uH` (TOR@KC, Sep 6), probed pregame with
`oddID=pitching_strikeouts-PLAYER_ID-game-ou-over,...-under`:

```
player entities: 24 (12 TOR, 12 KC — all 12 Toronto entries are hitters)
pitching_strikeouts entities: ['RANDY_DOBNAK_1_MLB']

Randy Dobnak       (KC, no move)        entity + prop, 6 books
Spencer Arrighetti (HOU->TOR, Aug 21)   no entity, no prop
```

Both are listed probable starters in the same game. Same result with
`oddsAvailable` omitted, so it isn't an availability filter.

Scale, checked against MLB's probable pitchers across every slate Aug 12 – Sep 10:
**642/798 starters priced (80.5%)**, a rate that has not moved in a month. At
least 99 of the 156 missing starts trace to this — 21 traded pitchers missing on
*every* start for their new club (Skubal,
Gausman, Luis Castillo, Robbie Ray, Freddy Peralta, Dean Kremer, Casey Mize,
Dustin May), plus five recent debuts with no entity.

Happy to send the full writeup with per-pitcher numbers. Mostly want to know
whether this is known, and whether player→team re-association on roster moves is
fixable feed-side.
