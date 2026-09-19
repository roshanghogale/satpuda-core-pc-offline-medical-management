#!/bin/bash
# Pull the newest server PostgreSQL dump to the local SSD.
#
# The server's own dumps live on the same virtual disk as PGDATA, so they die with
# the VM. This is the only copy that survives losing the box. Verifies the pulled
# file parses before keeping it.
set -euo pipefail

HOST=${1:-satpuda}
DEST=${2:-"/Volumes/Extreme SSD/projects/satpuda-bundles/postgres-backups"}
mkdir -p "$DEST"

newest=$(ssh "$HOST" 'ls -1t /var/backups/satpuda/*.dump 2>/dev/null | head -1')
[ -n "$newest" ] || { echo "no dump found on $HOST" >&2; exit 1; }

name=$(basename "$newest")
if [ -f "$DEST/$name" ]; then
    echo "already have $name"
else
    scp -q "$HOST:$newest" "$DEST/$name.part"
    mv "$DEST/$name.part" "$DEST/$name"
    echo "pulled $name ($(stat -f%z "$DEST/$name") bytes)"
fi

# Prove the local copy is restorable, not just present.
if command -v pg_restore >/dev/null 2>&1; then
    pg_restore -l "$DEST/$name" >/dev/null && echo "verified: $name parses"
else
    echo "note: pg_restore not installed locally, skipped verification"
fi

ls -1t "$DEST"/*.dump 2>/dev/null | tail -n +31 | xargs -r rm --
echo "$(ls -1 "$DEST"/*.dump 2>/dev/null | wc -l | tr -d ' ') dumps held locally"
