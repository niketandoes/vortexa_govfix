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
       "reason": entry.reason or ""
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

### 3.2 Live Tamper Demonstration Capability
For live judging:
- A controlled testing endpoint `POST /api/v1/demo/tamper` (or CLI script `scripts/tamper_demo.py`) that temporarily bypasses triggers on a test database to modify an old row (e.g. changing an audit outcome from `REFUSED` to `ACCEPTED`).
- Running the verification immediately flags the tampered row in red on the UI.

---

## 4. Adversarial Test Suite

The test suite formally validates all 7 core invariants under adversarial conditions:

| Invariant | Test Method | Expected Outcome |
| :--- | :--- | :--- |
| **State Skipping** | Attempt transition `ASSIGNED` ➔ `RESOLVED` directly | Refused with HTTP 422, `from_state` and allowed states in error, logged to audit. |
| **Unauthorized Role** | Citizen attempts `POST /transition` to `RESOLVED` | Refused with HTTP 403, logged as role violation in audit. |
| **Tamper Resistance** | Update a row directly in SQLite | Trigger aborts update; if forced, verification fails and identifies row. |
| **AI Boundary Isolation** | AST parsing of `src/ai/` codebase | Fails test if any import of `src/workflow` or write to `requests` is detected. |
| **Offline Resilience** | Disconnect external internet / remove API key | System switches cleanly to local TF-IDF fallback with zero errors. |
| **Idempotent Escalation** | Trigger escalation job multiple times at same clock time | Exactly one escalation audit entry is written; no duplicates. |
| **Ownership Enforcement** | Citizen A attempts to close/reopen Citizen B's complaint | Refused with HTTP 403, logged to audit. |

---

## 5. Live Demo Script (Step-by-Step for Judges)

1. **Step 1 — Complaint & AI Advisory:** Submit a complaint as Ramesh (Citizen). Point out that AI suggests `Water Leak` (94% confidence) and extracts the location, but status remains strictly `SUBMITTED`.
2. **Step 2 — Security Refusal (Citizen):** As citizen, attempt an unauthorized status change to `RESOLVED`. Show instant HTTP 403 and the refusal row appearing in the audit log.
3. **Step 3 — Invalid Move Refusal (Officer):** Switch to Officer Kumar. Try to mark the complaint `RESOLVED` while skipping `IN_PROGRESS`. Show the specific refusal reason naming the skipped state.
4. **Step 4 — Compliant Happy Path:** Supervisor assigns to Officer Kumar ➔ Officer starts work ➔ Officer resolves with notes ➔ Citizen confirms closure. Inspect complete audit trail.
5. **Step 5 — Time Fast-Forward & Escalation:** Advance simulated clock by +25 hours. Show ticket badge change to Amber (`Level 1 Overdue`), then advance +15 hours to trigger Red (`Level 2 Escalation`).
6. **Step 6 — Audit Verification & Tamper Catch:** Click "Verify Audit Chain" (Green checkmark). Click "Simulate Tamper" to alter record #3. Re-run verification: immediate Red alert flagging row #3!
7. **Step 7 — Offline Fallback:** Unset Gemini API key. Submit a new complaint. Show that local scikit-learn classifier tags the suggestion as `local_fallback` with full operational continuity.

---

## 6. Implementation Deliverables
1. `src/services/verification_service.py`: Cryptographic chain validator and canonical hash calculation.
2. `scripts/tamper_demo.py`: Script for judging demonstrations.
3. `tests/test_adversarial.py`: Full adversarial test suite.
4. `scripts/demo_walkthrough.py`: Automated CLI runner demonstrating all 7 steps.

---

## 7. Detailed Clarification Questions & Design Decisions
To prevent assumptions and hallucinations, please clarify the following points for Plan 06:
1. **Tamper Demo Access:** Should the `/api/v1/demo/tamper` endpoint be included directly in the application behind an `ENVIRONMENT=demo` flag, or strictly executed via a standalone python script?
2. **External Hash Anchor:** Should the latest audit chain hash be periodically exported to an external text file / receipt file (`audit_receipt.json`) to demonstrate off-chain anchoring?
3. **Interactive Demo Runner:** Would you like an automated interactive script (`python demo.py`) that steps through each of the 7 stages with pauses and colored console outputs for presentations?
