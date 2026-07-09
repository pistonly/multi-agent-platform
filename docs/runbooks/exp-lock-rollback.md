# Experiment Lock Rollback Runbook (CP-3.5)

Use this runbook if the experiment execution lock introduced in v2 misbehaves
in production (e.g., stale locks, runaway skip loop, lock-induced crash).

## 30-second rollback path

1. **Disable the lock globally** — set `MAP_HOST_NO_LOCK=1` and restart every
   host bridge instance:

   ```bash
   export MAP_HOST_NO_LOCK=1
   systemctl restart map-host-bridge.service   # or your service manager
   ```

   After this point, `cli/runtime/run_lock.py::ExperimentLockManager.disabled`
   short-circuits `acquire`/`release`, so pending experiments in the queue
   resume normal execution.

2. **Force-release any stuck lock** if a single experiment is wedged:

   ```bash
   map --persona host experiment force-release-lock --id <exp_id> \
       --reason "exp-lock rollback $(date -Iseconds)"
   ```

   This clears `lock_holder_experiment_id` and writes an audit log line with
   the `force_release_lock` keyword (see `cli/experiment_admin.py`).

3. **Verify pending queue is moving**:

   ```bash
   map --persona host status
   map --persona host topic list --status open
   ```

   You should see experiments in `approved → running → done` flow again.

## Pre / post comparison checklist

| Check                                                | Pre (broken)         | Post (rolled back) |
|------------------------------------------------------|----------------------|--------------------|
| `experiments.lock_holder_experiment_id` rows > 0     | many                 | none / cleared     |
| `host-bridge` log contains `lock_busy`              | every cycle          | absent             |
| `host-bridge` log contains `lock_stuck`             | ≥1                   | absent             |
| Pending experiments advancing past `approved`        | stalled              | flowing            |
| `MAP_HOST_NO_LOCK` env var                           | unset / 0            | 1                  |

## When to escalate

If after the rollback the queue is still stuck:

1. Inspect git checkpoints: `git log --grep='map: checkpoint before experiment'`
2. Manually move the offending experiment forward via the admin CLI
   (`map --persona host experiment approve / start / complete`).
3. Open an incident ticket and link the audit log lines.

## Re-enabling the lock

Once the underlying issue is fixed:

```bash
unset MAP_HOST_NO_LOCK
systemctl restart map-host-bridge.service
```

Re-run the integration tests to confirm:

```bash
pytest tests/test_experiment_lock.py tests/integration/test_experiment_lock.py \
       tests/integration/test_experiment_lock_stress.py \
       tests/integration/test_experiment_lock_skip.py
```

End of CP-3.5 runbook.
