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
│  - base_wall_time: Real System Clock                  │
│  - time_offset_seconds: float                          │
│  - is_frozen: bool                                     │
├────────────────────────────────────────────────────────┤
│  now() -> datetime:                                    │
│    returns (base_wall_time + time_offset_seconds)      │
└────────────────────────────────────────────────────────┘
```

### 2.2 Time Control API Endpoints
- `GET /api/v1/clock`: Returns `{ "current_time": "...", "offset_hours": 24.5, "is_frozen": false }`.
- `POST /api/v1/clock/advance`: Advances simulated clock by specified hours/minutes:
  ```json
  { "hours": 24 }
  ```
  Immediately triggers the escalation evaluation job.
- `POST /api/v1/clock/reset`: Clears offsets, synchronizing back to real-world time.

---

## 3. SLA Deadlines & Recomputation Rules

### 3.1 Initial Calculation
- When a complaint is created, its deadline is computed deterministically:
  $$\text{deadline} = \text{created\_at} + \text{SLA}[\text{category}]$$
- Default SLA for `Uncategorised` is 72 hours.
- **Rule:** AI never computes or modifies deadlines.

### 3.2 Category Confirmation & Deadline Recalculation
- When an authorized officer or supervisor confirms or modifies a complaint's category (e.g. from `Uncategorised` to `Water Leak`), the deadline is recomputed:
  $$\text{new\_deadline} = \text{created\_at} + \text{SLA}[\text{new\_category}]$$
- The adjustment is committed with an `audit` log entry capturing:
  - `action = 'RECALCULATE_DEADLINE'`
  - `reason = 'Category updated from Uncategorised (72h) to Water Leak (24h)'`
  - `old_deadline` and `new_deadline` in the record payload.

---

## 4. Overdue Detection & Escalation Ladder

### 4.1 Overdue Condition
A grievance is marked `is_overdue = true` if and only if:
1. $\text{current\_simulated\_time} > \text{deadline}$
2. State is active: $\text{state} \in \{\text{SUBMITTED}, \text{ACKNOWLEDGED}, \text{ASSIGNED}, \text{IN\_PROGRESS}, \text{REOPENED}\}$.
3. Grievances in terminal states (`RESOLVED`, `CLOSED`, `REJECTED`) are **never** overdue.

### 4.2 Multi-Level Escalation Ladder
Escalation is calculated based on the ratio of elapsed time against the category's SLA:
$$\text{elapsed\_ratio} = \frac{\text{current\_simulated\_time} - \text{created\_at}}{\text{SLA\_duration}}$$

| Elapsed Ratio | Target Escalation Level | Responsible Recipient | Display Badge |
| :--- | :--- | :--- | :--- |
| $< 1.0\times$ | `Level 0` | Assigned Officer | Normal (Green) |
| $\ge 1.0\times$ | `Level 1` | Supervisor | Overdue (Amber Warning) |
| $\ge 1.5\times$ | `Level 2` | Department Head | Critical (Red Alert) |

### 4.3 Idempotency Invariant
- The escalation job can run repeatedly without duplicating audit logs or corrupting state:
  $$\text{If } \text{target\_level} > \text{request.escalation\_level} \implies \text{Update Level and Log Escalation}$$
  $$\text{If } \text{target\_level} \le \text{request.escalation\_level} \implies \text{No-op}$$
- Each escalation writes an entry to `audit`:
  - `actor = 'SYSTEM'`
  - `actor_role = 'System'`
  - `action = 'ESCALATE_REQUEST'`
  - `reason = 'SLA elapsed ratio reached 1.0x (Level 1 escalation to Supervisor)'`

---

## 5. Implementation Deliverables
1. `src/services/clock_service.py`: Thread-safe clock provider with advance/reset capabilities and persistence in SQLite.
2. `src/services/escalation_service.py`: Idempotent evaluation engine scanning active tickets and applying escalation thresholds.
3. `src/routes/clock.py`: API routes for clock inspection and fast-forwarding.
4. `tests/test_escalation.py`:
   - Unit test creating a ticket with 24h SLA.
   - Advancing clock by 25h -> asserts `is_overdue == True` and `escalation_level == 1`.
   - Running escalation job again -> asserts no duplicate audit entries (idempotency).
   - Advancing clock by 37h (1.5x) -> asserts `escalation_level == 2`.
   - Marking ticket `RESOLVED` -> asserts ticket is no longer considered overdue.

---

## 6. Confirmed Decisions & Implementation Notes
1. **Escalation Execution:** Confirmed **Event-driven & On-demand**. Escalation checks execute immediately whenever the simulated clock is advanced (`/api/v1/clock/advance`) or when grievance queues are queried, eliminating unnecessary background polling threads while ensuring instant state accuracy.
2. **Calendar Time SLA:** SLA calculations use continuous calendar hours from `created_at` as defined in the VORTEXA problem statement.
3. **Escalation Irreversibility:** Escalation levels (Level 1, Level 2) remain elevated on active cases until the issue is formally marked `RESOLVED`, guaranteeing supervisor visibility.
4. **Idempotent Audit Log:** The engine checks `target_level > current_level` before logging escalations, ensuring multiple queries or clock ticks do not duplicate audit records.

