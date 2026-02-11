#!/usr/bin/env python3
import json
import os
import statsapi

# Configuration
DODGERS_TEAM_ID = 119
# Robust path handling (works from anywhere)
OUTPUT_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "apps",
    "dodgermetrics",
    "src",
    "data",
    "roster.json"
)

def fetch_roster():
    print(f"Fetching 40-man roster for Team ID {DODGERS_TEAM_ID}...")

    # Use the raw endpoint to get structured data
    # Note: We use the '40Man' roster type to get everyone, including injured stars
    raw_roster = statsapi.get(
        "team_roster",
        {"teamId": DODGERS_TEAM_ID, "rosterType": "40Man"}
    )

    players = []
    for player in raw_roster.get("roster", []):
        person = player.get("person", {})
        position = player.get("position", {})
        status = player.get("status", {})

        # Extract just what we need for the Hex Grid
        players.append({
            "id": person.get("id"),
            "name": person.get("fullName", "Unknown"),
            "jerseyNumber": player.get("jerseyNumber", "99"), # Default to 99 if missing
            "position": position.get("abbreviation", "DH"),
            "status": status.get("code", "A"), # A = Active, D10 = Injured, etc.
            "mlbHeadshot": f"https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current/w_213,q_auto:best/v1/people/{person.get('id')}/headshot/67/current"
        })

    # Sort by Jersey Number (Ascending)
    players.sort(key=lambda x: int(x['jerseyNumber']) if x['jerseyNumber'].isdigit() else 999)

    return players

def main():
    roster = fetch_roster()

    # Ensure output directory exists (recursively)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(roster, f, indent=2)

    print(f"✅ Success! Saved {len(roster)} players to:")
    print(f"   {os.path.abspath(OUTPUT_PATH)}")

if __name__ == "__main__":
    main()
