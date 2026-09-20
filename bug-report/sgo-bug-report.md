# Bug report: player→team association is not maintained after roster movement, so affected players' props never appear on any event

**Account:** visualize.ev@gmail.com · tier: amateur
**League:** MLB
**First observed:** 2026-08-12 (earliest data I have); still present 2026-09-10

## Summary

A player's team association appears to be set when his entity is created and
never maintained afterward. Any subsequent roster movement leaves the player
unattachable to an event, so his props are absent from the `players` map and
from `odds` entirely — not returned as unavailable odds, simply not present.

Three variants, all with the same effect:

| Variant | Example | `/v2/players` result |
|---|---|---|
| Stale team after a trade | `CLAY_HOLMES_1_MLB` | `teamID: NEW_YORK_METS_MLB` (a Cub since 2026-08-08) |
| No entity for a debut/call-up | `QUINN_MATHEWS_1_MLB` | empty — no entity exists |
| Entity with no team at all | `ROBERT_STOCK_1_MLB` | `teamID: ""` |

For MLB starting pitchers this is highly visible: an affected pitcher is missing
his `pitching_strikeouts` market on every start. Other books price these markets
normally, so the market exists — it just isn't in your feed.

## Reproduction

### 1. The player entity carries a stale team

```
GET /v2/players?playerID=CLAY_HOLMES_1_MLB&leagueID=MLB
  -> "teamID": "NEW_YORK_METS_MLB"      (traded to the Cubs on 2026-08-08)

GET /v2/players?playerID=SPENCER_ARRIGHETTI_1_MLB&leagueID=MLB
  -> "teamID": "HOUSTON_ASTROS_MLB"     (traded to Toronto on 2026-08-21)

GET /v2/players?playerID=RANDY_DOBNAK_1_MLB&leagueID=MLB
  -> "teamID": "KANSAS_CITY_ROYALS_MLB" (correct — and his props do come through)
```

### 2. The player is missing from his new team's event

Event `TIpBGFLk91l72AY3BnQv` — Cubs @ Marlins, 2026-09-06, first pitch
17:40Z. Clay Holmes is the Cubs' listed probable starter (MLB `gamePk` 823822,
`personId` 605280).

```
GET /v2/events?eventID=TIpBGFLk91l72AY3BnQv&leagueID=MLB&includeAltLines=true
    &oddID=pitching_strikeouts-PLAYER_ID-game-ou-over,pitching_strikeouts-PLAYER_ID-game-ou-under

players in payload: 24  — no CLAY_HOLMES_1_MLB entry
pitching_strikeouts odds: 2  — both TYLER_PHILLIPS_1_MLB (the Marlins starter)
```

Identical result with `oddsAvailable=true` omitted, so this is not an
availability filter.

### 3. Same event, one traded pitcher and one not

Event `JoOt8rGOFQFp4PSMu8uH` — Blue Jays @ Royals, 2026-09-06, first pitch
18:10Z, probed while `status.started == false`:

```
player entities: 24  (12 TOR, 12 KC — all 12 Toronto entries are position players)
pitching_strikeouts entities: ['RANDY_DOBNAK_1_MLB']

Randy Dobnak       (KC, no team change)        entity present, prop present, 6 books
Spencer Arrighetti (HOU -> TOR, 2026-08-21)    no entity, no prop
```

Both pitchers are listed probable starters for the same game. The one whose
`teamID` is current is priced across six bookmakers; the one whose `teamID` is
stale does not exist in the payload.

## Expected vs. actual

**Expected:** a player's `teamID` reflects his current club, and his props appear
on his current club's events.

**Actual:** `teamID` is frozen at the club he was on before the trade, and his
props appear on no event at all.

## Scope

Comparing MLB's listed probable starters against the pitchers your feed prices,
across every slate from 2026-08-12 to 2026-09-10:

**642 of 798 listed starters priced — 80.5%. 156 starts missing.**

That rate has not moved in a month. The same 80.5% held over the first 26 days
and still holds now, so this is not a transient gap that is repairing itself.

Of those missing starts, 75 belong to 21 pitchers who changed teams in 2026.
They are missing on *every* start for their new club, not intermittently:

| Pitcher | Move | Starts missed |
|---|---|---|
| Foster Griffin | WSH → CLE | 6 of 6 |
| José Soriano | LAA → TOR | 6 of 6 |
| Kevin Gausman | TOR → CHC | 6 of 6 |
| Dustin May | STL → MIL | 5 of 5 |
| Robbie Ray | SF → SD | 5 of 5 |
| Tyler Mahle | SF → ATL | 5 of 5 |
| Luis Castillo | SEA → CWS | 5 of 5 |
| Clay Holmes | NYM → CHC | 5 of 5 |
| Lake Bachar | MIA → PIT | 5 of 5 |
| Casey Mize | DET → SD | 5 of 5 |
| Dean Kremer | BAL → MIN | 5 of 5 |
| Freddy Peralta | NYM → TB | 4 of 4 |
| Tarik Skubal | DET → LAD | 4 of 4 |
| Spencer Arrighetti | HOU → TOR | 2 of 2 |

A further 24 missing starts belong to five pitchers who did *not* change teams,
and those turn out to be the other two variants:

```
GET /v2/players?playerID=<id>&leagueID=MLB

JACKSON_KENT_1_MLB     no entity      (6 starts missed)
QUINN_MATHEWS_1_MLB    no entity      (5 starts missed)
MATT_WILKINSON_1_MLB   no entity      (4 starts missed)
ETHAN_PECKO_1_MLB      no entity      (4 starts missed)
ROBERT_STOCK_1_MLB     teamID: ""     (5 starts missed)

TYLER_PHILLIPS_1_MLB   teamID: MIAMI_MARLINS_MLB   (control — priced normally)
```

The first four debuted in MLB during the collection window. So at minimum
**99 of the 156 missing starts (63%) are attributable to entity/team
association**, not to books declining to post a market. The real figure is
probably higher — the remaining misses are one-offs across 42 pitchers I did
not check individually.

Because the affected groups are defined by having been traded or promoted, the
gap is not a random sample. It removes exactly the deadline-acquisition and
debut populations, which are among the most in demand.

## Workarounds attempted (none work)

The data does not appear to be present under a different key — it looks like it
was never generated.

1. **Querying the old-team alias as an ID.** `/v2/players?playerID=CLAY_HOLMES_NEW_YORK_YANKEES_MLB`
   returns empty. Aliases are not resolvable identifiers.
2. **Requesting the odd explicitly, without the `PLAYER_ID` template.**
   `oddID=pitching_strikeouts-CLAY_HOLMES_1_MLB-game-ou-over,...-under` against
   event `TIpBGFLk91l72AY3BnQv` returns no odds at all.
3. **Filtering events by player.** `/v2/events?leagueID=MLB&playerID=CLAY_HOLMES_1_MLB`
   returns only **2024 Yankees** events, each with zero odds. Constraining to
   `startsAfter=2026-08-01` or `2026-09-01` returns **zero events** — so he is
   attached to no 2026 event at all, Mets or Cubs, even though `/v2/players`
   reports his `teamID` as `NEW_YORK_METS_MLB`.

That last result may be the most useful diagnostic: the `teamID` field and the
event associations disagree, and the event associations appear frozen at the era
the entity was created.

## Questions

1. Can player `teamID` be refreshed when a transaction occurs? The `aliases`
   field suggests team is baked into the identifier at creation
   (`CLAY_HOLMES_NEW_YORK_YANKEES_MLB` is listed as an alias for a player whose
   current `teamID` is the Mets), so this may need a re-association step rather
   than a field update.
2. What creates a player entity for a player making his MLB debut, and how long
   should it take? Four debuts from the last month still have none.
3. Is `teamID: ""` a known state, or does it indicate a broken record?
4. Is there a supported way to query props by player independent of team, so a
   consumer can work around this in the meantime?
5. Does this affect other leagues with in-season roster movement?
