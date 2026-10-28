#!/bin/sh
# Builds OSRM routing files from $DATA/work/combined.osm.pbf (runs inside the OSRM image).
# Builds into a temporary folder first, so a failed build never breaks the running engine.
set -e
DATA="${DATA:-/data}"
PBF="$DATA/work/combined.osm.pbf"
BUILD="$DATA/osrm/.build"

[ -f "$PBF" ] || { echo "ERROR: $PBF not found (run the merge step first)"; exit 1; }

rm -rf "$BUILD"; mkdir -p "$BUILD"
# osrm-extract writes its output next to the input, so link the input in.
ln -sf "$PBF" "$BUILD/map.osm.pbf"

echo "==> extract  (profile: ${OSRM_PROFILE:-car})"
osrm-extract -p "/opt/${OSRM_PROFILE:-car}.lua" "$BUILD/map.osm.pbf"
echo "==> partition"
osrm-partition "$BUILD/map.osrm"
echo "==> customize"
osrm-customize "$BUILD/map.osrm"

rm -f "$BUILD/map.osm.pbf" "$DATA"/osrm/map.osrm*
mv "$BUILD"/map.osrm* "$DATA/osrm/"
rm -rf "$BUILD"
echo "==> done: $DATA/osrm/map.osrm"
