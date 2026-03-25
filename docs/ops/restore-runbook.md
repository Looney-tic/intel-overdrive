# Neon Restore Runbook

## When to use this

Use this runbook when you need to recover from accidental data deletion, data corruption, or a failed migration. Neon retains 7 days of write-ahead log for point-in-time restore on all plans.

## Prerequisites

- Neon CLI installed: `npm install -g neonctl` or use the Neon console
- `NEON_API_KEY` environment variable set (from https://console.neon.tech/app/settings/api-keys)
- Project ID and branch name available (from `.env` or Neon console)

## Restore procedure

### Step 1: Identify the restore point

Find the timestamp before the incident in Neon's write-ahead log:

```bash
# List recent operations to find the incident timestamp
neon operations list --project-id <PROJECT_ID>
```

Target timestamp format: `2026-03-15T14:30:00Z`

### Step 2: Create a restore branch

```bash
# Create a new branch at the target timestamp (non-destructive)
neon branches create \
  --project-id <PROJECT_ID> \
  --name restore-$(date +%Y%m%d-%H%M) \
  --parent main \
  --point-in-time <TIMESTAMP>
```

This creates an isolated branch — main is unaffected.

### Step 3: Verify the restore

Connect to the restore branch and confirm data integrity:

```bash
# Get connection string for restore branch
neon connection-string \
  --project-id <PROJECT_ID> \
  --branch restore-$(date +%Y%m%d-%H%M)

# Run verification queries
psql "$RESTORE_DATABASE_URL" -c "SELECT COUNT(*) FROM intel_items;"
psql "$RESTORE_DATABASE_URL" -c "SELECT MAX(created_at) FROM intel_items;"
psql "$RESTORE_DATABASE_URL" -c "SELECT COUNT(*) FROM sources;"
```

Expected: row counts consistent with pre-incident state.

### Step 4: Promote the restore branch (if correct)

Option A — Switch the application DATABASE_URL to the restore branch:

1. Update `.env` on VPS: `DATABASE_URL=<restore-branch-connection-string>`
2. `docker compose -f docker-compose.prod.yml restart api fast-worker slow-worker`
3. Verify application health: `curl https://api.yourdomain.com/v1/health`

Option B — Copy data from restore branch to main (if only partial recovery needed):
Use `pg_dump` from restore branch and `pg_restore` into main. Scope to affected tables only.

### Step 5: Cleanup

After confirming production is stable, delete the restore branch:

```bash
neon branches delete \
  --project-id <PROJECT_ID> \
  --name restore-<date>
```

## Acceptance test (OPS-02 gate)

Before Phase 8 is marked complete, perform this test once against the production Neon project:

1. Create a test branch at a known timestamp (e.g., 1 hour ago)
2. Connect and run `SELECT COUNT(*) FROM intel_items`
3. Confirm row count matches expected state
4. Delete the test branch

Document the result (timestamp, row count) as a comment in this file below:

<!-- OPS-02 VERIFIED: [date] [branch] [intel_items count] -->
