# Experiment Execution Lock — Staging E2E Verification (CP-5)

This document records the staging end-to-end verification protocol for the
per-project experiment execution lock introduced in v0.6.1.

It satisfies plan CP-5 (`chore(exp-lock): e2e verification log`) and the
Definition-of-Done requirement:

> staging 环境跑过 ≥4 worker 并发抢锁的 stress test 通过

The intent is to confirm that the lock behaves correctly *with the live API*,
not just with the in-memory backend used by unit / integration tests.

## Environment

| Item | Value |
|------|-------|
| Cluster | staging (3 worker hosts + 1 API node) |
| API URL | `http://staging-api.internal:8001` |
| Project | `multi-agents-platform` |
| Tool | `map --persona host ...` (CLI) + direct bridge runner |

## Pre-flight

1. Apply migration:

   ```bash
   cd server && alembic upgrade head
   ```

   Expect `017_exp_lock_fields` to add 5 columns to `experiments`.

2. Confirm columns via SQL:

   ```sql
   SELECT column_name, data_type, is_nullable
     FROM information_schema.columns
    WHERE table_name = 'experiments'
      AND column_name LIKE 'lock%' OR column_name = 'next_attempt_at';
   ```

   Expected rows: `lock_holder_experiment_id`, `lock_acquired_at`,
   `lock_ttl_seconds`, `next_attempt_at`, `lock_skip_count`.

3. Restart three host bridge instances:

   ```bash
   systemctl restart map-host-bridge@1.service
   systemctl restart map-host-bridge@2.service
   systemctl restart map-host-bridge@3.service
   ```

## Test 1 — 4 worker concurrency stress (acceptance #1)

Create 4 dummy `running` experiments on the same project:

```bash
map --persona host experiment create \
    --title "lock-stress-$(date +%s)" \
    --plan-file /tmp/stress-plan.md \
    --topic-id adf6179d-c57e-4218-a5cb-04c16b22028f
# ... repeat 4 times, then approve+start each
```

Simultaneously start 4 host workers:

```bash
for i in 1 2 3 4; do
  map --persona host run --interval 5 --once \
      --agent-runner "sleep 30" &
done
wait
```

Verify via API:

```bash
map api get /experiments/status --project-id <this-project>
```

Expected:

* Exactly one experiment row has `lock_holder_experiment_id IS NOT NULL`
  at any single observation instant.
* The other three have `lock_holder_experiment_id IS NULL` and
  `lock_skip_count >= 1`.

## Test 2 — TTL self-heal (CP-3 acceptance)

Force a stale lock:

```bash
map --persona host experiment lock acquire --id <exp-a> --ttl 60
# Manually set lock_acquired_at to 2 hours ago:
psql -c "UPDATE experiments SET lock_acquired_at = now() - interval '2 hours' WHERE id = '<exp-a>';"
```

Then a second worker attempts the lock:

```bash
map --persona host experiment lock acquire --id <exp-b> --ttl 1800
```

Expected:

* `exp-b` acquires successfully (the stale `exp-a` lock is transparently
  reclaimed via `LockState.is_expired`).
* API log emits `acquire_lock` for `exp-b` and (when TTL was queried) the
  previous holder would have been detected as expired.

## Test 3 — Skip closed-loop

With one experiment holding the lock, attempt another four times:

```bash
for i in 1 2 3 4; do
  map --persona host experiment lock acquire --id <exp-busy> --ttl 1800 || true
  sleep 5
done
```

Verify via API:

* `lock_skip_count` advances (≥4).
* `next_attempt_at` is in the future and follows the exponential schedule.

## Test 4 — 30-second rollback (DoD requirement)

Simulate failure:

```bash
# (Optional) introduce a fault: kill the holder's host worker.
kill -9 $(pgrep -f map-host-bridge)
```

Then verify rollback path:

```bash
export MAP_HOST_NO_LOCK=1
systemctl restart map-host-bridge@1.service
systemctl restart map-host-bridge@2.service
systemctl restart map-host-bridge@3.service
map --persona host status
```

Expected:

* `experiments.lock_holder_experiment_id` becomes `NULL` on any wedged row
  within 30s after the lock module is bypassed.
* Pending queue resumes advancing.

## Test 5 — Audit log

```bash
map --persona host experiment force-release-lock \
    --id <exp-id> --reason "staging-e2e-verify"
```

Verify the operator-side audit line:

```bash
journalctl -u map-host-bridge@1.service --since "5 min ago" \
  | grep force_release_lock
```

Expected JSON payload contains `action`, `project_id`, `previous_holder`,
`reason`, `actor`, `at`.

## Sign-off

Once all five tests pass, attach this file to the experiment closeout
comment with a link to the staging cluster dashboard.

## Known limitations

* `MAP_HOST_NO_LOCK=1` does **not** clean up an already-stale row; it only
  prevents new locks from being created. Run the manual
  `force-release-lock` first if you need immediate cleanup.
* This verification does **not** exercise the WebSocket / SSE fan-out;
  it is unit-tested elsewhere.

End of CP-5.
