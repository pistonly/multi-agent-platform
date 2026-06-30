# Experiment Execution Lock — Schema Diff Baseline (CP-0)

This document records the schema baseline captured before introducing the
per-project experiment execution lock. It satisfies plan CP-0.

## Methodology

We read the source of truth:

* `server/domain/models.py::Experiment`
* `server/domain/state_machine.py` (`ExperimentPhase` enum)
* `sdk/python/map_types/enums.py::ExperimentPhase`
* latest alembic revision: `016_topic_decisions_actions`

## State machine baseline

`ExperimentPhase` enum (current values):

```python
class ExperimentPhase(str, enum.Enum):
    draft = "draft"
    review = "review"
    approved = "approved"
    running = "running"
    done = "done"
    cancelled = "cancelled"
```

There is **no dedicated `executing` phase**. The closest existing phase is
`running`, which the host bridge transitions the experiment into after
`experiment_start` and out of via `experiment_complete`.

### Decision (CP-0)

> **We do NOT extend the state machine with `executing` in v2.**

Rationale:

* `running` already represents the actively-executing phase for experiments.
* Adding a new enum value would require a migration on every existing enum
  column, breaking clients pinned to the old version.
* The lock module treats `phase == running` AND
  `lock_holder_experiment_id IS NOT NULL` as the "currently executing"
  composite condition. This is sufficient for v2.

If the v3 design needs to distinguish "running but not yet under lock" from
"running under lock", we can introduce `executing` later without breaking
this contract.

## Field diff (CP-3)

The following fields are added to `experiments`:

| Column                          | Type         | Nullable | Default | Notes                                 |
|---------------------------------|--------------|----------|---------|---------------------------------------|
| `lock_holder_experiment_id`     | `Uuid`       | yes      | NULL    | FK-style soft reference; same table.  |
| `lock_acquired_at`              | `DateTime(tz)` | yes    | NULL    | UTC; cleared when lock is released.   |
| `lock_ttl_seconds`              | `Integer`    | yes      | NULL    | Stale-lock self-heal.                 |
| `next_attempt_at`               | `DateTime(tz)` | yes    | NULL    | Used by skip closed-loop.             |
| `lock_skip_count`               | `Integer`    | no       | 0       | Counter for `lock_stuck` threshold.   |

Indices:

* partial index on `(project_id) WHERE lock_holder_experiment_id IS NOT NULL`
  to make `is_held` lookups cheap.

## Migration

See `alembic/versions/017_exp_lock_fields.py`. Migration order:

1. Add columns as NULLABLE.
2. Backfill `lock_skip_count = 0` (default already applied).
3. Sweep stale `running` experiments older than 1 hour:
   ```sql
   UPDATE experiments
      SET phase = 'idle' -- (see migration; in practice we set to 'cancelled' since 'idle' is not a valid phase)
    WHERE phase = 'running'
      AND updated_at < now() - interval '1 hour';
   ```
   The migration actually sets the phase to `cancelled` with
   `description = 'auto-cancelled by exp-lock migration 017'` so the row is
   not lost but is no longer considered active.

## Validation steps

```bash
map api get /experiments/status --project-id <this-project>
map api get /openapi.json  # confirm new fields in schema
```

End of CP-0 baseline.
