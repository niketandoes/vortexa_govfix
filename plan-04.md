# Plan 04: AI Advisory Layer & Structural Boundary

## 1. Executive Summary & Objective
Plan 04 designs and implements the AI Advisory Layer for GovFix. The layer assists municipal officers by proposing ticket categories, extracting physical location cues and urgency levels, and drafting citizen response templates. Crucially, the AI is architecturally isolated: it writes advisory outputs solely to an isolated suggestions table, cannot alter ticket state, and includes a completely offline, zero-dependency local machine-learning fallback.

---

## 2. Core Architectural Boundary & Invariants

The design follows the fundamental VORTEXA rule: **AI suggests, deterministic rules decide, humans confirm, and everything is logged.**

### 2.1 The Structural Firewall
```
┌────────────────────────────────┐
│       AI Advisory Layer        │
│   (LLM / Offline Fallback)     │
└───────────────┬────────────────┘
                │ Writes ONLY to:
                ▼
┌────────────────────────────────┐
│      ai_suggestions Table      │
│  (kind, payload, source, etc.) │
└───────────────┬────────────────┘
                │ Read by:
                ▼
┌────────────────────────────────┐       Human Officer /       ┌────────────────────────┐
│     Officer UI Workspace       │ ────► Supervisor Confirms ─►│    Workflow Service    │
│    (Displays as suggestion)    │       (Explicit Click)      │ (Deterministic Engine) │
└────────────────────────────────┘                             └────────────────────────┘
```

### 2.2 Boundary Invariants
1. **Zero State Mutation:** The AI service has no database write access to the `requests` table or `audit` table.
2. **No Reverse Imports:** The AI module (`src/ai/`) does not import the `workflow` service or any transition execution functions.
3. **Automated Architectural Test:** A test parses the Abstract Syntax Tree (AST) of the AI module to verify that no prohibited imports or state-mutation calls exist.
4. **Human-in-the-Loop:** Suggestions have zero legal or operational effect until an authorized human acts on them (e.g., an officer clicking "Confirm Category", which triggers a distinct audited workflow action).

---

## 3. Advisory Capabilities

### 3.1 Categorisation
- Analyzes complaint text and maps it into one of the 4 standard categories: `Water Leak`, `Garbage Collection`, `Streetlight`, `Pothole`.
- Returns a confidence score (0.0 to 1.0).

### 3.2 Information Extraction
- Extracts:
  - **Location Cues:** Street name, landmark, ward/sector, or intersection.
  - **Urgency Level:** `HIGH`, `MEDIUM`, or `LOW` based on hazard cues (e.g., "gushing water", "exposed wire", "near school").

### 3.3 Citizen Reply Drafting
- Generates an empathetic, formal response acknowledging the issue, explaining the standard SLA window, and providing expected next steps.

### 3.4 Duplicate Detection (Optional Extension)
- Computes cosine similarity of TF-IDF vectors against open complaints in the same category.
- Flags potential duplicates if similarity > 0.75.

---

## 4. Dual-Path Implementation Architecture

### 4.1 Primary Path: Gemini LLM API
- Utilizes the Google GenAI SDK (`google-genai` / `gemini-1.5-flash`).
- Structured JSON output mode to guarantee parseable schema:
  ```json
  {
    "category": "Water Leak",
    "confidence": 0.94,
    "location": "Opposite City Hospital Gate 3, MG Road",
    "urgency": "HIGH",
    "draft_reply": "Dear Citizen, Thank you for reporting the water pipe rupture near City Hospital. Our emergency water works team has been alerted under our 24-hour SLA."
  }
  ```
- Tagged with `source = 'gemini_llm'`.

### 4.2 Local Offline Fallback (Zero External Dependency)
- Fully functional when offline, without API keys, or if external requests fail.
- **Classification:** `scikit-learn` `TfidfVectorizer` paired with `LogisticRegression` or `MultinomialNB`, trained on a bundled synthetic dataset of ~100 municipal complaints.
- **Extraction:** Named regex pattern matching for streets, roads, sectors, and hazard keywords.
- **Drafting:** Template-based generator with slot filling for category and location.
- Tagged with `source = 'local_fallback'`.

---

## 5. Implementation Deliverables
1. `src/ai/interface.py`: Abstract Base Class `AIEngine` defining `suggest(complaint_text) -> SuggestionPayload`.
2. `src/ai/local_classifier.py`: Offline scikit-learn model, vectorizer, and pre-trained weights/training script.
3. `src/ai/gemini_engine.py`: Google Gemini API client with automatic fallback on failure or missing API key.
4. `src/ai/dataset.json`: Synthetic dataset of 100 categorized municipal complaints.
5. `src/services/suggestion_service.py`: Service coordinating suggestion generation and storing records in `ai_suggestions`.
6. `tests/test_ai_boundary.py`:
   - Unit test asserting AST of `src/ai` contains zero imports of `src/workflow`.
   - Unit test asserting fallback accuracy on test complaints.
   - Unit test verifying graceful fallback when Gemini key is missing.

---

## 6. Confirmed Decisions & Implementation Notes
1. **Dual-Path Strategy:** Confirmed dual-path with silent fallback. If `GEMINI_API_KEY` is present and functional, Gemini LLM is invoked; if missing or unreachable, the system automatically falls back to the local `scikit-learn` TF-IDF classifier without crashing or interrupting operations.
2. **Suggestion Metadata:** Every suggestion record in `ai_suggestions` is tagged with `source='gemini_llm'` or `source='local_fallback'`.
3. **Architectural Isolation:** Strict structural firewall enforced via automated AST unit tests to verify no import of `src/workflow` or write to `requests` exists in `src/ai`.

