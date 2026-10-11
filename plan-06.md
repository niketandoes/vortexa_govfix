# Plan 06: Hardening, Cryptographic Chain Verification & Adversarial Demo

## 1. Executive Summary & Objective
Plan 06 delivers the security hardening, cryptographic audit chain verification, adversarial automated test suite, and the end-to-end interactive demo harness for GovFix. It proves to hackathon judges that the municipal engine cannot be fooled by skipped states, unauthorized roles, or database tampering.

---

## 2. Cryptographic Hash-Chain Architecture

### 2.1 Canonical Hashing Formula
Every row written to the `audit` table is cryptographically bound to its predecessor via SHA-256.

```
Record N-1 ──[ hash ]──┐
                        ▼
                ┌───────────────┐
                │ previous_hash │
                ├───────────────┤
                │ Canonical     │──► SHA-256 ──► Record N [ hash ]
                │ Content       │
                └───────────────┘
```

1. **Genesis Entry:**
   $$\text{previous\_hash}_1 = \text{"0000000000000000000000000000000000000000000000000000000000000000"}$$
2. **Canonical Content Serialization:**
   To prevent whitespace or ordering mismatches, the payload is serialized with sorted keys:
   ```python
   canonical_str = json.dumps({
       "timestamp": entry.timestamp,
       "actor": entry.actor,
       "actor_role": entry.actor_role,
       "action": entry.action,
       "request_id": entry.request_id,
       "from_state": entry.from_state,
       "to_state": entry.to_state,
       "outcome": entry.outcome,
       "reason": entry.reason or "",
       "details_json": canonicalize_json(entry.details_json),
       "dedupe_key": entry.dedupe_key or "",
       "simulated_at": entry.simulated_at or ""
   }, sort_keys=True, separators=(',', ':'))
   ```
3. **Current Entry Hash:**
   $$\text{hash}_N = \text{SHA256}(\text{previous\_hash}_N + \text{canonical\_str})$$

---

## 3. Audit Verification & Tamper Detection Engine

### 3.1 Verification Algorithm
The verification service traverses all audit records in ascending order:
1. Verifies that `entry[i].previous_hash == entry[i-1].hash`.
2. Computes the canonical hash of `entry[i]` and checks if `computed_hash == entry[i].hash`.
3. If any discrepancy is found, it terminates traversal and returns:
   ```json
   {
     "is_valid": false,
     "error": "TAMPER_DETECTED",
     "tampered_record_id": 14,
     "expected_hash": "a4f89c...",
     "actual_hash": "e113b2...",
     "tampered_at": "2026-10-11T12:00:00Z"
   }
   ```
4. If all records match:
   ```json
   {
     "is_valid": true,
     "verified_records_count": 87,
     "chain_head": "f8a19b..."
   }
   ```

This internally anchored chain detects modified content and broken links when an attacker does not also recompute all later hashes. It does not by itself prove that the final row was deleted or resist an administrator who rewrites the entire chain. Stronger detection requires a trusted external receipt containing the expected chain head and record count.

### 3.2 Live Tamper Demonstration Capability
For live judging, `scripts/tamper_demo.py` first copies the seeded database to a disposable demo path, removes the copy's mutation trigger, and modifies an old row (for example changing `REFUSED` to `ACCEPTED`). Verification against that copy immediately flags the affected row in red. The normal application database and web API expose no trigger-bypass capability.

---

## 4. Adversarial Test Suite

The test suite formally validates all 8 core invariants under adversarial conditions:

| Invariant | Test Method | Expected Outcome |
| :--- | :--- | :--- |
| **State Skipping** | Attempt transition `ASSIGNED` ➔ `RESOLVED` directly | Refused with HTTP 422, `from_state` and allowed states in error, logged to audit. |
| **Unauthorized Role** | From `IN_PROGRESS`, Citizen attempts the otherwise valid transition to `RESOLVED` | Refused with HTTP 403, logged as role violation in audit. |
| **Field Officer Isolation** | Field Officer B attempts to start work or resolve Field Officer A's ticket | Refused with HTTP 403, logged as unauthorized assignment attempt. |
| **Tamper Resistance** | Update a row directly in SQLite | Trigger aborts update; if forced on demo copy, verification fails and identifies row. |
| **AI Boundary Isolation** | AST checks plus runtime capability tests | Fails if AI imports workflow/request repositories or can mutate `requests`/`audit`. |
| **Offline Resilience** | Disconnect external internet / remove API key | System switches cleanly to local TF-IDF fallback with zero errors. |
| **Idempotent Escalation** | Run concurrent evaluation at the same clock time | Exactly one escalation audit entry per request, level, and SLA cycle. |
| **Ownership Enforcement** | Citizen A attempts to close/reopen Citizen B's complaint | Refused with HTTP 403, logged to audit. |

---

## 5. Live Demo Script (Step-by-Step for Judges across 5 Personas)

The walkthrough uses named records so the happy path, dispute, and escalation proofs do not invalidate one another:

1. **Step 1 — Citizen Submission & AI Advisory:** Submit Pothole Complaint A as Ramesh Patel (`USR-CIT-01`) on `/citizen`. Receive unique tracking token `REQ-2026-0001`. Show the AI suggestion for category and location preview while state remains `SUBMITTED`.
2. **Step 2 — Triage Officer Review & Assignment:** Log in as Triage Officer Anil Kumar (`USR-OFF-01`) on `/officer`. Acknowledge complaint (`SUBMITTED → ACKNOWLEDGED`). Assign it to Field Officer Vikram Singh (`USR-FLD-01`). Confirm the ticket moves to `ASSIGNED` and leaves the triage intake queue.
3. **Step 3 — Field Officer Queue & Invalid Transition Blocked:** Switch to `/field-officer` as Vikram Singh (`USR-FLD-01`). Observe that Sunita Rao (`USR-FLD-02`) cannot see Vikram's case. Attempt `ASSIGNED → RESOLVED` directly; verify HTTP 422 refusal, ticket unchanged, and refusal logged to audit.
4. **Step 4 — Field Execution & Citizen Closure:** Vikram Singh moves `ASSIGNED → IN_PROGRESS`. Switch to Ramesh Patel and attempt `IN_PROGRESS → RESOLVED`; verify HTTP 403 role violation. Vikram Singh submits resolution notes and photos (`IN_PROGRESS → RESOLVED`). Switch to Ramesh Patel on `/citizen` and confirm resolution (`RESOLVED → CLOSED`).
5. **Step 5 — Resolution Dispute & Supervisor Reassignment:** On another resolved ticket, citizen disputes resolution (`RESOLVED → REOPENED` with reason). Supervisor Rajesh Gupta on `/supervisor` inspects the case and reassigns it to Field Officer Sunita Rao (`REOPENED → ASSIGNED`). Sunita Rao accepts and executes work.
6. **Step 6 — SLA Escalation Demo:** Inspect seeded overdue Complaint B (`REQ-2026-0006`). Advance simulated clock by 25 hours on `/supervisor` widget. Verify Amber `Level 1` escalation to Supervisor. Advance by 11 additional hours (36h total, 1.5x) to verify Red `Level 2` escalation to Municipal Head. Confirm zero duplicate audit events.
7. **Step 7 — Read-Only Audit Chain & Tamper Detection:** Open `/audit` console. Verify all accepted and refused events are logged with SHA-256 hashes. Click "Verify Audit Chain" to show green chain verification. Run `python scripts/tamper_demo.py` against disposable database copy and show immediate red alert identifying the exact tampered row ID.
8. **Step 8 — Offline AI Fallback:** Unset Gemini API key; submit new complaint. Bundled scikit-learn TF-IDF classifier produces `local_fallback` suggestion without interruption.

---

## 6. Implementation Deliverables
1. `src/services/verification_service.py`: Cryptographic chain validator and canonical hash calculation.
2. `scripts/tamper_demo.py`: Script for judging demonstrations.
3. `tests/test_adversarial.py`: Full adversarial test suite.
4. `scripts/demo_walkthrough.py`: Automated CLI runner demonstrating all 8 steps.

---

## 7. Confirmed Design Decisions

1. **Tamper Demo Access:** Tampering is available only through a standalone script operating on a disposable database copy; no HTTP bypass endpoint is shipped.
2. **External Hash Anchor:** External anchoring is outside the MVP. Documentation therefore states the tail-deletion limitation explicitly.
3. **Interactive Runner:** `scripts/demo_walkthrough.py` automates the seven stages with named records and asserts the expected HTTP status and starting state before each action.
