# Disaster Recovery

The backup/restore procedure for the production Postgres database, and a
record of the restore test that was actually run against it — per
COMMERCIAL_READINESS_AUDIT.md P0-15: "Do not consider backup complete
until a restore test succeeds."

## Scope

Covers the Postgres database only (every table: users, resumes, resume
versions, job curations and their versions, subscriptions, AI usage logs,
AI jobs, Stripe webhook events, policy acceptances, cookie consent,
coupons). Uploaded resume files (local disk / a Docker volume today) are
a separate concern — see `COMMERCIAL_READINESS_AUDIT.md`'s note that
migrating them to real object storage (S3-compatible) is still an open
P1 item; a local-volume backup strategy for `backend-media` should be
revisited once that migration happens.

## RPO / RTO

| | Target | Why |
|---|---|---|
| **RPO** (max acceptable data loss) | 24 hours | Backups run once daily (see below). A customer's most recent day of resume edits/job curations is the realistic exposure window. |
| **RTO** (max acceptable downtime) | ~30 minutes | Dominated by provisioning a fresh Postgres instance/container, not the restore itself — the actual `pg_restore` step took under a minute against the real dataset size tested (see the drill below). Revisit this once production data volume is known; a multi-GB database restores slower than the ~100KB test case here. |

These are planning targets for a pre-revenue/early-revenue product, not a
contractual SLA. Tighten RPO (e.g. hourly WAL archiving / point-in-time
recovery) if/when the cost of losing a day of data actually matters more
than the added operational complexity — premature for where this product
is today.

## Backup mechanism

`docker-compose.yml`'s `backup` service (a plain `postgres:16-alpine`
container — no extra image needed, it already has `pg_dump`) runs
`scripts/backup_db.sh` once on startup and then every 24 hours:

- Dumps the full database in Postgres's custom format (`pg_dump -Fc`) to
  the `backups-data` volume, timestamped
  (`intelliresume-<UTC-timestamp>.dump`).
- Prunes anything older than `BACKUP_RETENTION_DAYS` (default 14).

**This is a starting point, not a complete solution**: `backups-data` is
a Docker volume on the same host as the database it's backing up. A
disk/host failure takes out both the database and its backups together.
Before this is a real disaster recovery plan, wire up an off-host copy —
`rclone`/`aws s3 sync`/whatever object storage the deployment target
already has — copying out of that volume on the same schedule. Not done
here because no object storage is configured yet (see the audit's P1
item on S3-compatible storage).

## Restore procedure

`scripts/restore_db.sh <path-to-backup-file>` — **destructive**: drops
and recreates the target database, then restores into it. Requires the
same `POSTGRES_*` env vars as the backup script, plus `POSTGRES_ADMIN_DB`
(defaults to `postgres`) to connect to while dropping/recreating.

```bash
# From a host with the postgres client tools and network access to the
# database (e.g. `docker compose exec backup sh`):
./scripts/restore_db.sh /backups/intelliresume-20260821T190000Z.dump

# Then, in case the backup predates a migration applied since:
python manage.py migrate
```

The script pauses 10 seconds before dropping anything, as a last chance
to Ctrl+C out of a mistaken invocation.

## Restore test actually performed

Run 2026-08-21, against a real local Postgres 18 instance (not a
simulation or a dry read of the script) — the schema and data below match
exactly what `restore_db.sh` does, one step at a time:

1. Created an isolated test database (`intelliresume_dr_test`, separate
   from any other database on that instance).
2. Ran the full Django migration set against it — the actual current
   schema, not a fixture.
3. Seeded realistic data through the ORM: a user, a policy acceptance, a
   resume with `parsed_data` and an ATS score, a resume version, an
   active Pro subscription, and an AI usage log row.
4. `pg_dump -Fc` — produced a 98KB backup file.
5. `dropdb` — the database was completely destroyed, confirmed via
   `psql -l` no longer listing it (this is the disaster being simulated).
6. `createdb` + `pg_restore` — recreated empty, then restored from the
   step-4 backup file.
7. Verified through the ORM (not a raw SQL spot-check) that every seeded
   row survived intact: same user name/email/phone, the same policy
   acceptance, the resume's `parsed_data` (`{"skills": ["Python",
   "Disaster Recovery"]}`) and ATS score (88.50) byte-for-byte, the
   resume version, the subscription (`pro`/`active`), and the AI usage
   log.
8. Cleaned up: dropped the test database and deleted the backup file —
   the host was left in its original state.

**Result: pass.** Every row survived the drop/restore cycle unchanged.

This validates the *mechanism* (pg_dump → drop → pg_restore actually
round-trips this application's schema correctly) — it does not yet
validate restore time or backup size at real production data volume,
since none exists yet. Re-run this drill against a production-sized
database once one exists, and update the RTO estimate above with the
real number instead of the small-dataset one.

## Who's responsible

Not yet assigned — this is a solo/small-team product pre-launch. Assign
an explicit owner (even if it's "the founder") before real customer data
exists, and put a recurring calendar reminder to actually re-run this
drill (quarterly is a reasonable starting cadence) rather than trusting
that an unattended cron job is working just because no one has
complained.
