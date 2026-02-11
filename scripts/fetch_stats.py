#!/usr/bin/env python3
import json
import os
import statsapi
import time

# Configuration
TARGET_SEASON = 2025  # Force 2025 so we have data to see right now
SCRIPT_DIR = os.path.dirname(__file__)
ROSTER_PATH = os.path.join(SCRIPT_DIR, "..", "apps", "dodgermetrics", "src", "data", "roster.json")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "..", "apps", "dodgermetrics", "src", "data", "stats.json")

def fetch_player_stats(player_id, position):
    """Fetch stats for a single player."""
    stats = {}

    # Pitcher Identifiers
    pitcher_codes = ["P", "RHP", "LHP"]

    try:
        # 1. Fetch Pitching if they are a Pitcher or Two-Way
        if position in pitcher_codes or position == "TWP":
            # Note: We pass 'season' explicitly to get last year's data
            p_data = statsapi.player_stat_data(
                player_id, group="pitching", type="season", sportId=1, season=TARGET_SEASON
            )
            if p_data.get('stats'):
                stats['pitching'] = p_data['stats'][0].get('stats', {})

        # 2. Fetch Hitting if they are NOT a pitcher (or are Two-Way)
        if position not in pitcher_codes or position == "TWP":
            h_data = statsapi.player_stat_data(
                player_id, group="hitting", type="season", sportId=1, season=TARGET_SEASON
            )
            if h_data.get('stats'):
                stats['hitting'] = h_data['stats'][0].get('stats', {})

        return stats

    except Exception as e:
        print(f"  ❌ Error fetching stats for player {player_id}: {e}")
        return {}

def main():
    if not os.path.exists(ROSTER_PATH):
        print(f"Error: Roster file not found at {ROSTER_PATH}")
        return

    with open(ROSTER_PATH, "r") as f:
        roster = json.load(f)

    print(f"Fetching {TARGET_SEASON} stats for {len(roster)} players...")
    print("------------------------------------------------")

    all_stats = {}

    for i, player in enumerate(roster):
        player_id = player["id"]
        name = player["name"]
        position = player["position"]

        print(f"Processing [{i+1}/{len(roster)}]: {name} ({position})")

        player_stats = fetch_player_stats(player_id, position)

        # Only add to file if we actually found stats
        if player_stats:
            all_stats[player_id] = player_stats

        # Be polite to the API
        time.sleep(0.2)

    # Ensure output directory exists
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_stats, f, indent=2)

    print("------------------------------------------------")
    print(f"✅ Success! Saved stats for {len(all_stats)} players.")
    print(f"   path: {os.path.abspath(OUTPUT_PATH)}")

if __name__ == "__main__":
    main()
