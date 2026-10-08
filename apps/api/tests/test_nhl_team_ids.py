"""NHL provider identities should converge on official tricodes."""

from routers.nhl import get_tricode_from_sgo_team_id


def test_utah_current_and_legacy_sgo_ids_map_to_uta():
    assert get_tricode_from_sgo_team_id("UTAH_MAMMOTH_NHL") == "UTA"
    assert get_tricode_from_sgo_team_id("UTAH_HOCKEY_CLUB_NHL") == "UTA"
