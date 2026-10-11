# Plan 04: AI Advisory Layer & Structural Boundary

## 1. Executive Summary & Objective
Plan 04 designs and implements the AI Advisory Layer for GovFix. The layer assists municipal officers by proposing ticket categories, extracting physical location cues and urgency levels, and drafting citizen response templates. Crucially, the AI is isolated through a narrow application capability: it can submit suggestion records but cannot alter ticket state. The local fallback runs offline with bundled Python dependencies.

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
1. **No State-Mutation Capability:** The AI engine receives complaint text and a narrow `SuggestionRepository.insert_suggestion(...)` capability, never a general database connection or request repository.
2. **No Reverse Imports:** The AI module (`src/ai/`) does not import the `workflow` service or any transition execution functions.
3. **Automated Architectural Tests:** AST tests reject prohibited workflow/repository imports, and runtime tests prove that the injected capability can create suggestions but cannot mutate requests or audit rows.
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

### 4.2 Local Offline Fallback (No External Service)
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
5. `src/services/suggestion_service.py`: Owns the narrow suggestion repository, coordinates generation, and stores records in `ai_suggestions` without exposing the general database connection to AI engines.
6. `tests/test_ai_boundary.py`:
   - Unit test asserting AST of `src/ai` contains zero imports of `src/workflow`.
   - Runtime capability test proving AI engines cannot mutate `requests` or `audit`.
   - Unit test asserting fallback accuracy on test complaints.
   - Unit test verifying graceful fallback when Gemini key is missing.

---

## 6. Confirmed Decisions & Implementation Notes
1. **Dual-Path Strategy:** Confirmed dual-path with silent fallback. If `GEMINI_API_KEY` is present and functional, Gemini LLM is invoked; if missing or unreachable, the system automatically falls back to the local `scikit-learn` TF-IDF classifier without crashing or interrupting operations.
2. **API Key Input:** Provided via the environment variable `GEMINI_API_KEY`.
3. **Training Dataset (`src/ai/dataset.json`):** 100 realistic synthetic grievances featuring bilingual / Hinglish Indian municipal context (e.g. Ward numbers, MG Road, water leak bursts, streetlights, potholes) generated automatically and bundled in repository.
4. **Duplicate Detection Scope:** Kept as a documented extension for now to keep the live MVP streamlined on core categorisation, location/urgency extraction, and reply drafting.
5. **Suggestion Metadata:** Every suggestion record in `ai_suggestions` is tagged with `source='gemini_llm'` or `source='local_fallback'`.
6. **Architectural Isolation:** Application-level capability isolation is enforced through the narrow suggestion repository, AST checks, and runtime mutation tests.


