#!/bin/sh
# Merges every .osm.pbf in $DATA/osm into $DATA/work/combined.osm.pbf
# and writes the combined bounding box to $DATA/work/bbox.txt.
# Runs inside the dr-osm-merge container (DATA=/data) or on a host with osmium installed.
set -e
DATA="${DATA:-/data}"
mkdir -p "$DATA/work"
OUT="$DATA/work/combined.osm.pbf"
TMP="$DATA/work/combined.tmp.osm.pbf"

set -- "$DATA"/osm/*.osm.pbf
[ -f "$1" ] || { echo "ERROR: no .osm.pbf files in $DATA/osm"; exit 1; }

echo "Input regions:"
for f in "$@"; do echo "  - $(basename "$f")"; done

rm -f "$TMP"
if [ "$#" -eq 1 ]; then
  # one region: no merge needed, just copy (hard link when possible)
  ln -f "$1" "$TMP" 2>/dev/null || cp "$1" "$TMP"
else
  # Geofabrik extracts are sorted, so a streaming merge works and removes
  # duplicate objects where regions overlap at their borders.
  osmium merge --progress -O -o "$TMP" "$@"
fi
mv -f "$TMP" "$OUT"

# Combined bounding box from the file headers (Geofabrik files include one).
minx=; miny=; maxx=; maxy=
for f in "$@"; do
  b=$(osmium fileinfo -g header.boxes "$f" 2>/dev/null | head -n1 | tr -d '()' | tr ' ' ',')
  [ -n "$b" ] || continue
  IFS=, read -r x1 y1 x2 y2 <<EOT
$b
EOT
  minx=$(awk -v a="${minx:-$x1}" -v b="$x1" 'BEGIN{print (b<a)?b:a}')
  miny=$(awk -v a="${miny:-$y1}" -v b="$y1" 'BEGIN{print (b<a)?b:a}')
  maxx=$(awk -v a="${maxx:-$x2}" -v b="$x2" 'BEGIN{print (b>a)?b:a}')
  maxy=$(awk -v a="${maxy:-$y2}" -v b="$y2" 'BEGIN{print (b>a)?b:a}')
done
if [ -n "$minx" ]; then
  echo "$minx,$miny,$maxx,$maxy" > "$DATA/work/bbox.txt"
  echo "Area covered: $minx,$miny,$maxx,$maxy"
else
  rm -f "$DATA/work/bbox.txt"
  echo "No bounding box in file headers; the API will use DR_BBOX from .env"
fi
echo "Merged -> $OUT"
