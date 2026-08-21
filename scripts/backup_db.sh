#!/bin/sh
# Dumps the Postgres database to a timestamped file and prunes backups
# older than BACKUP_RETENTION_DAYS. Runs inside the `backup` service in
# docker-compose.yml (a postgres:16-alpine container, which already has
# pg_dump) — see DISASTER_RECOVERY.md for the full procedure, RPO/RTO, and
# a real restore test performed against this exact script's counterpart
# (restore_db.sh).
#
# Required env: POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER,
# POSTGRES_PASSWORD (docker-compose.yml already sets all of these for the
# `backup` service). BACKUP_DIR defaults to /backups (the mounted volume).

set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILENAME="${BACKUP_DIR}/intelliresume-${TIMESTAMP}.dump"

mkdir -p "$BACKUP_DIR"

echo "[backup_db] Starting backup -> ${FILENAME}"
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
  -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
  -Fc -f "$FILENAME" "$POSTGRES_DB"

echo "[backup_db] Backup complete: $(du -h "$FILENAME" | cut -f1)"

echo "[backup_db] Pruning backups older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -name 'intelliresume-*.dump' -mtime "+${RETENTION_DAYS}" -print -delete
