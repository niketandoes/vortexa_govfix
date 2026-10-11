# Plan 05: Simulated Clock, SLA Deadline & Escalation Engine

## 1. Executive Summary & Objective
Plan 05 implements the time-governance engine of GovFix. It includes an administratively controllable simulated clock, automated deterministic SLA deadline computations, and an idempotent multi-level escalation job. This allows testing and demonstrating SLA breaches, overdue detection, and departmental escalations in seconds during live evaluations.

---

## 2. Simulated Clock Architecture

### 2.1 The Clock Provider Abstraction
To ensure deterministic reproducibility, no workflow service or database query calls `datetime.now()` directly. Instead, all temporal logic injects a centralized `ClockService`.

```
┌────────────────────────────────────────────────────────┐
│                   ClockService                         │
│  - wall_now: Real UTC System Clock                    │
│  - offset_seconds: integer                             │
│  - is_frozen: bool                                     │
│  - frozen_at: optional simulated UTC timestamp         │
├────────────────────────────────────────────────────────┤
│  now() -> datetime:                                    │
│    frozen ? frozen_at : wall_now + offset_seconds      │
└────────────────────────────────────────────────────────┘
```

### 2.2 Time Control API Endpoints
- `GET /api/v1/clock`: Returns `{ "current_time": "...", "offset_hours": 24.5, "is_frozen": false }`.
- `POST /api/v1/clock/advance`: Advances simulated clock by specified hours/minutes:
  ```json
  { "hours": 24 }
  ```
  Immediately triggers the escalation evaluation job.
- `POST /api/v1/clock/freeze`: Stores the current simulated timestamp in `frozen_at`; repeated `now()` calls remain fixed until advance or reset.
- `POST /api/v1/clock/reset`: Clears offsets, synchronizing back to real-world time.

The singleton `clock_state` row persists offset/freeze state. Advance is relative to current simulated time. Reset clears the offset and freeze state. Audit records retain wall-clock `timestamp` plus optional `simulated_at`; audit order is always the increasing audit ID, so resetting simulated time cannot reorder history.

---

## 3. SLA Deadlines & Recomputation Rules

### 3.1 Initial Calculation
- When a complaint is created, its deadline is computed deterministically:
  $$\text{deadline} = \text{sla\_started\_at} + \text{SLA}[\text{category}]$$
- Default SLA for `Uncategorised` is 72 hours.
- **Rule:** AI never computes or modifies deadlines.

### 3.2 Category Confirmation & Deadline Recalculation
- When an authorized officer or supervisor confirms or modifies a complaint's category (e.g. from `Uncategorised` to `Water Leak`), the deadline is recomputed:
  $$\text{new\_deadline} = \text{sla\_started\_at} + \text{SLA}[\text{new\_category}] + \text{total\_paused\_seconds}$$
- The adjustment is committed with an `audit` log entry capturing:
  - `action = 'RECALCULATE_DEADLINE'`
  - `reason = 'Category updated from Uncategorised (72h) to Water Leak (24h)'`
  - `old_deadline` and `new_deadline` in the record payload.

The dedicated category command is available to Officers and Supervisors in active states and stores old/new category and deadline values in `details_json`.

### 3.3 Reopen SLA Cycle
On `RESOLVED -> REOPENED`, or `CLOSED -> REOPENED` within seven days of `closed_at`, the workflow transaction sets `sla_started_at` to simulated now, increments `sla_cycle` and `reopen_count`, resets `escalation_level` and `total_paused_seconds` to 0, clears `paused_at`, and calculates a fresh deadline from the confirmed category. The required reopen reason, source state, and all reset values are stored in transition audit details.

### 3.4 Clock Pausing During `NEEDS_INFO`
When an officer requests additional information from a citizen (`ACKNOWLEDGED → NEEDS_INFO`):
1. **Pause Action:** The engine sets `paused_at = current_simulated_time`.
2. **Clock Halting:** While `paused_at IS NOT NULL`, the effective elapsed resolution time is frozen. The ticket cannot breach SLA or advance escalation levels while waiting for citizen information.
3. **Resume Action:** When the citizen submits the requested details (`NEEDS_INFO → ACKNOWLEDGED`):
   $$\text{paused\_delta} = \text{current\_simulated\_time} - \text{paused\_at}$$
   $$\text{total\_paused\_seconds} = \text{total\_paused\_seconds} + \text{paused\_delta}$$
   $$\text{new\_deadline} = \text{deadline} + \text{paused\_delta}$$
   $$\text{new\_triage\_deadline} = \text{triage\_deadline} + \text{paused\_delta}\quad\text{if triaged\_at is NULL}$$
   $$\text{paused\_at} = \text{NULL}$$
4. An audit record is created capturing `CLOCK_RESUMED` with the extended deadline in `details_json`.

---

## 4. Overdue Detection & Escalation Ladder

### 4.1 Overdue & At-Risk Conditions
A grievance is evaluated based on its active elapsed time. While currently paused, `paused_at` replaces the moving clock so At-Risk status also remains frozen:
$$\text{effective\_now} = \begin{cases}\text{paused\_at}, & \text{if paused}\cr\text{current\_simulated\_time}, & \text{otherwise}\end{cases}$$
$$\text{effective\_elapsed} = (\text{effective\_now} - \text{sla\_started\_at}) - \text{total\_paused\_seconds}$$
$$\text{elapsed\_ratio} = \frac{\text{effective\_elapsed}}{\text{SLA\_duration}}$$

1. **At-Risk Warning ($\ge 0.75\times$ elapsed):**
   - When elapsed time reaches 75% of allotted SLA, the grievance is flagged as `At Risk`.
   - Displays an Amber warning indicator in Field Officer and Supervisor dashboards; the Triage Officer sees the separate 2-hour triage target.
2. **Overdue Condition ($\ge 1.0\times$ elapsed):**
   - Marked `is_overdue = true` when $\text{current\_simulated\_time} > \text{deadline}$ and `paused_at IS NULL`.
   - State must be active: $\text{state} \in \{\text{SUBMITTED}, \text{ACKNOWLEDGED}, \text{ASSIGNED}, \text{IN\_PROGRESS}, \text{REOPENED}\}$.
   - Grievances in terminal states (`RESOLVED`, `CLOSED`, `REJECTED`) or waiting in `NEEDS_INFO` are **never** overdue.

### 4.2 Multi-Level Escalation Ladder

| Elapsed Ratio | Escalation Level | Responsible Recipient | Display Badge & Action |
| :--- | :--- | :--- | :--- |
| $< 0.75\times$ | `Level 0` | Assigned Field Officer | Normal (Green) |
| $\ge 0.75\times$ | `Level 0` (At Risk) | Assigned Field Officer | At Risk (Amber Warning Badge) |
| $\ge 1.0\times$ | `Level 1` | Supervisor (`USR-SUP-01`) | Overdue (Supervisor Escalation Alert) |
| $\ge 1.5\times$ | `Level 2` | Municipal / Dept Head (`USR-SUP-01`) | Critical (Department Head Alert) |

> **Crucial Rule:** Escalation is an independent attribute (`escalation_level = 0, 1, 2`) and audit event, **never** an arbitrary status transition. An overdue complaint remains `IN_PROGRESS` while its escalation level increases.

The Level 2 threshold is exactly `1.5x` the category SLA. The MVP does not also use a separate `+8 hours` rule because that would produce conflicting thresholds across categories.

### 4.3 Idempotency Invariant
- The escalation evaluation job is idempotent. It can run repeatedly without duplicating audit logs or corrupting state:
  $$\text{If } \text{target\_level} > \text{request.escalation\_level} \implies \text{Update Level and Log Escalation}$$
  $$\text{If } \text{target\_level} \le \text{request.escalation\_level} \implies \text{No-op}$$
- Each escalation writes an entry to `audit`:
  - `actor = 'SYSTEM'`
  - `actor_role = 'System'`
  - `action = 'ESCALATE_REQUEST'`
  - `reason = 'SLA elapsed ratio reached 1.0x (Level 1 escalation to Supervisor)'`
- Deduplication key `escalation:{request_id}:{sla_cycle}:{target_level}` prevents race-condition duplicates.

---

## 5. Implementation Deliverables
1. `src/services/clock_service.py`: Thread-safe clock provider with advance/reset capabilities and SQLite persistence.
2. `src/services/escalation_service.py`: Idempotent evaluation engine scanning active tickets, handling clock pauses, and applying escalation thresholds.
3. `src/routes/clock.py`: API routes for clock inspection and fast-forwarding (`+1h`, `+12h`, `+24h`, `+72h`, `reset`).
4. `tests/test_escalation.py`:
   - Unit test creating a ticket with 24h SLA.
   - Advancing by 18h (0.75x) -> asserts `is_at_risk == True`.
   - Advancing by 7 additional hours (25h total, 1.04x) -> asserts `is_overdue == True` and `escalation_level == 1`.
   - Pausing clock via `NEEDS_INFO` -> asserts time advancement does not increase elapsed time.
   - Resuming clock via `ACKNOWLEDGED` -> asserts deadline extended by paused duration.
   - Advancing by 11 additional hours (36h total, 1.5x) -> asserts `escalation_level == 2`.
   - Marking ticket `RESOLVED` -> asserts ticket is no longer overdue.
   - Reopening ticket -> asserts new SLA cycle, fresh deadline, and escalation level 0.
   - Verifies duplicate escalation events are prevented.

---

## 6. Confirmed Decisions & Implementation Notes
1. **Escalation Execution:** Confirmed **Event-driven & On-demand**. Escalation checks execute immediately whenever the simulated clock is advanced (`/api/v1/clock/advance`) or when grievance queues are queried, eliminating unnecessary background polling threads while ensuring instant state accuracy.
2. **Calendar Time SLA:** SLA calculations use continuous calendar hours from the current `sla_started_at`; the first cycle starts at `created_at`.
3. **Escalation Lifecycle:** Levels remain elevated during an active SLA cycle until resolution. Reopening creates a new cycle and resets the level to 0 in the same transaction.
4. **Idempotent Audit Log:** Conditional updates, serialized writes, and the escalation `dedupe_key` uniqueness guard prevent duplicates.
5. **Level 2 Recipient:** The MVP uses `USR-SUP-01`, the seeded Municipal Head with authorization role `Supervisor`; a separate notification-recipient model may be added later.
6. **Separate Triage Target:** `triage_deadline` begins at submission, is extended by citizen-waiting pauses until `triaged_at` is set, and is a dashboard target rather than an escalation level.

