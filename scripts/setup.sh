#!/usr/bin/env bash
# Builds routing data and map tiles from every .osm.pbf in $DR_DATA_DIR/osm/.
#
#   Add a region:  copy another .osm.pbf into $DR_DATA_DIR/osm/ and run this again.
#   Update:        replace a file with a newer download and run this again.
#   Remove:        delete the file and run this again.
#
# Only the steps whose inputs changed are rebuilt. Builds happen in a temporary
# folder, so the running stack keeps working until the new data is ready.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { echo "Create .env first:  cp .env.example .env"; exit 1; }
set -a; . ./.env; set +a

DATA="${DR_DATA_DIR:-./data}"
FORCE="${DR_FORCE_REBUILD:-0}"
PROFILE="${DR_OSRM_PROFILE:-car}"
mkdir -p "$DATA"/{osm,work,osrm,tiles,tiles-sources,tmp,postgres}

shopt -s nullglob
inputs=("$DATA"/osm/*.osm.pbf)
shopt -u nullglob
[ ${#inputs[@]} -gt 0 ] || { echo "No .osm.pbf files in $DATA/osm/. Copy at least one there."; exit 1; }

# Signature of the input files: name, size and modification time of each.
sig=$(for f in "${inputs[@]}"; do
        printf '%s %s %s\n' "$(basename "$f")" "$(stat -c %s "$f")" "$(stat -c %Y "$f")"
      done | sort)
same() { [ "$FORCE" != "1" ] && [ -f "$1" ] && [ "$(cat "$1")" = "$2" ]; }

echo "Regions in $DATA/osm:"
printf '  %s\n' "${inputs[@]##*/}"
changed=0

echo; echo "### 1/4  Merge regions"
if same "$DATA/work/inputs.sig" "$sig" && [ -f "$DATA/work/combined.osm.pbf" ]; then
  echo "unchanged, skipping"
else
  docker compose --profile setup build dr-osm-merge
  docker compose --profile setup run --rm dr-osm-merge
  echo "$sig" > "$DATA/work/inputs.sig"
  changed=1
fi

echo; echo "### 2/4  Routing data (OSRM, profile: $PROFILE)"
osrm_sig="$sig
profile=$PROFILE"
if same "$DATA/osrm/built.sig" "$osrm_sig" && [ -f "$DATA/osrm/map.osrm.mldgr" ]; then
  echo "unchanged, skipping"
else
  echo "This can take 10-60 minutes and several GB of RAM, depending on the regions."
  docker compose --profile setup run --rm dr-osrm-prepare
  echo "$osrm_sig" > "$DATA/osrm/built.sig"
  changed=1
fi

echo; echo "### 3/4  Map tiles (.pmtiles)"
if same "$DATA/tiles/built.sig" "$sig" && [ -f "$DATA/tiles/map.pmtiles" ]; then
  echo "unchanged, skipping"
else
  rm -rf "$DATA/tiles/.build"; mkdir -p "$DATA/tiles/.build"
  docker compose --profile setup run --rm dr-tiles-build
  mv -f "$DATA/tiles/.build/map.pmtiles" "$DATA/tiles/map.pmtiles"
  rm -rf "$DATA/tiles/.build" "$DATA/tmp"/*
  echo "$sig" > "$DATA/tiles/built.sig"
  changed=1
fi

echo; echo "### 4/4  Address search (suggestions in the picker)"
if [ "${DR_GEOCODE:-1}" != "1" ]; then
  echo "disabled (DR_GEOCODE=0), skipping"
else
  geo_sig="$sig
style=$(cat scripts/geocode.lua scripts/geocode-build.sql | md5sum | cut -d' ' -f1)"
  if same "$DATA/work/geo.sig" "$geo_sig"; then
    echo "unchanged, skipping"
  else
    echo "Imports places, streets, landmarks and addresses into the database (search keeps working meanwhile)."
    docker compose --profile setup build dr-geo-import
    docker compose --profile setup run --rm dr-geo-import
    echo "$geo_sig" > "$DATA/work/geo.sig"
  fi
fi

echo
if [ "$changed" = 1 ] && [ -n "$(docker compose ps -q dr-osrm 2>/dev/null)" ]; then
  echo "Restarting routing engine and API to load the new data..."
  docker compose restart dr-osrm dr-api
fi
echo "Done. Start (or keep running) the stack with:  docker compose up -d --build"
