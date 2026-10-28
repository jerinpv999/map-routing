#!/bin/sh
# Builds the address search index from $DATA/work/combined.osm.pbf into the dr_geo schema.
# Runs inside the dr-geo-import container (needs PGHOST/PGUSER/PGPASSWORD/PGDATABASE).
# Imports into a staging schema first, so search keeps working until the swap at the end.
set -e
DATA="${DATA:-/data}"
PBF="$DATA/work/combined.osm.pbf"
[ -f "$PBF" ] || { echo "ERROR: $PBF not found (run the merge step first)"; exit 1; }

echo "==> waiting for the database"
for i in $(seq 1 60); do pg_isready -q && break; sleep 2; done

psql -v ON_ERROR_STOP=1 -q <<SQL
CREATE EXTENSION IF NOT EXISTS postgis;
DROP SCHEMA IF EXISTS dr_geo_import CASCADE;
CREATE SCHEMA dr_geo_import;
SQL

echo "==> importing places, streets, landmarks and addresses"
DR_GEO_SCHEMA=dr_geo_import osm2pgsql \
  --output=flex --style="${SCRIPTS:-/scripts}/geocode.lua" \
  --slim --drop --middle-schema=dr_geo_import \
  --cache="${GEO_CACHE_MB:-2000}" --number-processes="${GEO_PROCESSES:-4}" \
  --database="$PGDATABASE" \
  "$PBF"

echo "==> building the search index (area names, merged streets, text index)"
psql -v ON_ERROR_STOP=1 -f "${SCRIPTS:-/scripts}/geocode-build.sql"
echo "==> address search ready"
