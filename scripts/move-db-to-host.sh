#!/usr/bin/env bash
# One-time: copy the database from the old docker volume (dr-routing-pgdata)
# into $DR_DATA_DIR/postgres on the host. Only needed if you ran an earlier
# version of this project. Your data in the volume is left untouched.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
DATA="${DR_DATA_DIR:-./data}"
DEST="$(cd "$DATA" 2>/dev/null && pwd || (mkdir -p "$DATA" && cd "$DATA" && pwd))/postgres"

if ! docker volume inspect dr-routing-pgdata >/dev/null 2>&1; then
  echo "No old volume dr-routing-pgdata found. Nothing to move."; exit 0
fi
mkdir -p "$DEST"
if [ -n "$(ls -A "$DEST" 2>/dev/null)" ]; then
  echo "$DEST is not empty. Refusing to overwrite."; exit 1
fi

echo "Stopping the database..."
docker stop dr-postgis >/dev/null 2>&1 || true
echo "Copying volume dr-routing-pgdata -> $DEST"
docker run --rm -v dr-routing-pgdata:/from:ro -v "$DEST":/to alpine sh -c 'cp -a /from/. /to/'
echo "Done. Start the stack:  docker compose up -d"
echo "When everything works, you can delete the old volume:  docker volume rm dr-routing-pgdata"
