-- 20260920000000 revoked execute from public only. Supabase's default
-- privileges grant execute on new public functions to anon and authenticated
-- directly, so both could still call the insert function. RLS rejected their
-- inserts; this closes the outer wall, matching append_nba_player_prop_odds.
revoke all on function public.append_nfl_player_prop_odds(jsonb) from anon, authenticated;
