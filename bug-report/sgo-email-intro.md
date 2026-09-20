Hi <name>,

You checked in a few months back after I picked up an API key — taking you up on
that now, because I've run into something I think you'll want to know about.

I've been collecting MLB pitcher strikeout props daily since mid-August and
cross-checking what comes back against MLB's own listed probable starters. About
20% of starters never appear in the feed at all, and the cause turns out to be
that a player's team association is set when his entity is created and never
updated afterward. Traded pitchers, players called up mid-season, and at least
one player whose `teamID` is an empty string are all unattachable to an event, so
their props are absent — not returned as unavailable odds, just not there.

Full writeup below with reproductions, the specific event and player IDs, and
per-pitcher numbers across 30 days. Short version: 642 of 798 listed starters
priced, and at least 99 of the 156 missing starts trace to this rather than to
books declining to post a market. The rate has not moved in a month. Tarik
Skubal, Kevin Gausman, Luis Castillo and Robbie Ray are among the pitchers
missing on every start since changing teams.

I've checked the obvious client-side workarounds and none of them recover the
data, so I don't think this is something I can route around on my end.

Happy to run any diagnostic that would help, or to re-check once something ships.

Thanks,
Jarrett
