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

The test suite formally validates all 7 core invariants under adversarial conditions:

| Invariant | Test Method | Expected Outcome |
| :--- | :--- | :--- |
| **State Skipping** | Attempt transition `ASSIGNED` ➔ `RESOLVED` directly | Refused with HTTP 422, `from_state` and allowed states in error, logged to audit. |
| **Unauthorized Role** | From `IN_PROGRESS`, Citizen attempts the otherwise valid transition to `RESOLVED` | Refused with HTTP 403, logged as role violation in audit. |
| **Tamper Resistance** | Update a row directly in SQLite | Trigger aborts update; if forced, verification fails and identifies row. |
| **AI Boundary Isolation** | AST checks plus runtime capability tests | Fails if AI imports workflow/request repositories or can mutate `requests`/`audit`. |
| **Offline Resilience** | Disconnect external internet / remove API key | System switches cleanly to local TF-IDF fallback with zero errors. |
| **Idempotent Escalation** | Run concurrent evaluation at the same clock time | Exactly one escalation audit entry per request, level, and SLA cycle. |
| **Ownership Enforcement** | Citizen A attempts to close/reopen Citizen B's complaint | Refused with HTTP 403, logged to audit. |

---

## 5. Live Demo Script (Step-by-Step for Judges)

The walkthrough uses two named records so the happy path and escalation proof do not invalidate one another.

1. **Step 1 — Complaint A and AI Advisory:** Submit Complaint A as Ramesh. Show the `Water Leak` suggestion and extracted location while the confirmed category remains `Uncategorised` and state remains `SUBMITTED`.
2. **Step 2 — Acknowledge and Assign:** Officer Kumar acknowledges Complaint A from the intake queue. Supervisor Gupta assigns it to Kumar. Confirm that the state is now `ASSIGNED`.
3. **Step 3 — Invalid Move Refusal:** As Officer Kumar, request `ASSIGNED -> RESOLVED`. Show HTTP 422 and the refusal naming `IN_PROGRESS` as the valid next state.
4. **Step 4 — Role Refusal and Happy Path:** Kumar starts work so Complaint A becomes `IN_PROGRESS`. Switch to Ramesh and request the otherwise valid `IN_PROGRESS -> RESOLVED` edge; show HTTP 403 and the logged role refusal. Switch back to Kumar, resolve with non-empty notes, then let Ramesh close it.
5. **Step 5 — Complaint B Escalation:** Select the separately seeded active 24-hour Water Leak complaint. Advance to 25 elapsed hours for Amber Level 1, then advance 11 additional hours to 36 total for Red Level 2. Complaint A remains closed and is never used for this step.
6. **Step 6 — Audit Verification and Tamper Catch:** Verify the normal chain successfully. Run `scripts/tamper_demo.py` to create and alter a disposable database copy, point verification at that copy, and show the first compromised row. The normal database is untouched.
7. **Step 7 — Offline Fallback:** Unset the Gemini API key and submit a new complaint. Show that the bundled scikit-learn classifier produces a `local_fallback` suggestion without affecting workflow behavior.

---

## 6. Implementation Deliverables
1. `src/services/verification_service.py`: Cryptographic chain validator and canonical hash calculation.
2. `scripts/tamper_demo.py`: Script for judging demonstrations.
3. `tests/test_adversarial.py`: Full adversarial test suite.
4. `scripts/demo_walkthrough.py`: Automated CLI runner demonstrating all 7 steps.

---

## 7. Confirmed Design Decisions

1. **Tamper Demo Access:** Tampering is available only through a standalone script operating on a disposable database copy; no HTTP bypass endpoint is shipped.
2. **External Hash Anchor:** External anchoring is outside the MVP. Documentation therefore states the tail-deletion limitation explicitly.
3. **Interactive Runner:** `scripts/demo_walkthrough.py` automates the seven stages with named records and asserts the expected HTTP status and starting state before each action.
