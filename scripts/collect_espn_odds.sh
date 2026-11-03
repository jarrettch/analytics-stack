#!/usr/bin/env bash
set -euo pipefail

sport="${1:?Usage: collect_espn_odds.sh <sport> <offset>}"
offset="${2:?Usage: collect_espn_odds.sh <sport> <offset>}"

echo "Fetching ${sport} odds (offset=${offset})..."
if response=$(curl \
  --retry 4 \
  --retry-delay 15 \
  --retry-max-time 180 \
  --silent \
  --show-error \
  --fail \
  --max-time 120 \
  --request POST \
  --header "Authorization: Bearer ${CRON_SECRET}" \
  "$API_URL/api/${sport}/cron/fetch-odds?offset=${offset}"); then
  printf 'Response: %s\n' "$response"
else
  curl_status=$?
  printf 'Request failed:\n%s\n' "$response" >&2
  exit "$curl_status"
fi

# Still required: collector failures come back as HTTP 200.
if ! jq -e '.success == true' >/dev/null <<<"$response"; then
  printf 'Failed to fetch %s odds:\n%s\n' "$sport" "$response" >&2
  exit 1
fi

fresh=$(jq -r '.fresh_from_api // 0' <<<"$response")
candidates=$(jq -r '.pregame_snapshot_candidates // 0' <<<"$response")
inserted=$(jq -r '.snapshots_inserted // 0' <<<"$response")
deduplicated=$(jq -r '.snapshots_deduplicated // 0' <<<"$response")
persistence_ok=$(jq -r '.snapshot_persistence_ok // false' <<<"$response")

echo "${sport} odds: fresh=${fresh} candidates=${candidates} inserted=${inserted} deduplicated=${deduplicated}"
# fresh=0 is normal on days without games.

if [ "$candidates" -gt 0 ] && [ "$persistence_ok" != "true" ]; then
  echo "${sport} odds history persistence failed" >&2
  exit 1
fi
