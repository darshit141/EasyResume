#!/bin/sh
# Restores a backup produced by backup_db.sh into POSTGRES_DB. Destructive:
# drops and recreates the target database first, so this is for disaster
# recovery, not a routine operation. See DISASTER_RECOVERY.md for the full
# procedure — this script implements exactly the steps that were run and
# verified there against a real Postgres instance.
#
# Usage (from inside the `backup` container, or any host with the same
# POSTGRES_* env vars and pg_dump/pg_restore/psql available):
#   ./restore_db.sh /backups/intelliresume-20260821T190000Z.dump
#
# Required env: POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER,
# POSTGRES_PASSWORD, and an *admin* database to connect to for the
# drop/create step (POSTGRES_ADMIN_DB, defaults to "postgres").

set -eu

BACKUP_FILE="${1:?Usage: restore_db.sh <path-to-backup-file>}"
ADMIN_DB="${POSTGRES_ADMIN_DB:-postgres}"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "[restore_db] Backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

echo "[restore_db] About to DROP and recreate database '${POSTGRES_DB}' on ${POSTGRES_HOST}:${POSTGRES_PORT}."
echo "[restore_db] This is destructive. Press Ctrl+C within 10 seconds to abort."
sleep 10

export PGPASSWORD="$POSTGRES_PASSWORD"

echo "[restore_db] Dropping database ${POSTGRES_DB} (if it exists)"
dropdb -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB"

echo "[restore_db] Creating a fresh, empty database ${POSTGRES_DB}"
createdb -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" "$POSTGRES_DB"

echo "[restore_db] Restoring from ${BACKUP_FILE}"
pg_restore -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$BACKUP_FILE"

echo "[restore_db] Restore complete. Run 'python manage.py migrate' next in case the"
echo "[restore_db] backup predates a migration that has since been applied."
