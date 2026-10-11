"""Local Machine Learning Fallback Engine for GovFix.

Provides offline categorisation, location & urgency extraction, and
reply drafting using scikit-learn (TF-IDF + MultinomialNB) with a
pure-Python resilient fallback and heuristic rule-based extractors.

ZERO EXTERNAL API CALLS. RUNS 100% LOCALLY AND OFFLINE.
"""

import json
import math
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

from .interface import AIEngine, SuggestionPayload

DATASET_PATH = Path(__file__).parent / "dataset.json"

SLA_HOURS = {
    "Water Leak": 24,
    "Garbage Collection": 48,
    "Streetlight": 72,
    "Pothole": 168,
    "Uncategorised": 72
}

HIGH_URGENCY_KEYWORDS = [
    "burst", "gushing", "flood", "flooding", "hazard", "electric shock",
    "exposed wire", "sparking", "accident", "hospital", "deadly", "danger",
    "dangerous", "crater", "caved-in", "fire", "emergency", "blackout",
    "severe", "children", "patients", "collapse"
]

LOW_URGENCY_KEYWORDS = [
    "flicker", "flickering", "slow", "timer", "minor", "dustbin",
    "unpaved patch", "swept", "small"
]


class PurePythonNaiveBayes:
    """Zero-dependency pure-Python TF-IDF Naive Bayes fallback."""

    def __init__(self):
        self.doc_count = 0
        self.vocab: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self.class_word_counts: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.class_total_words: Dict[str, float] = defaultdict(float)
        self.class_doc_counts: Dict[str, int] = defaultdict(int)
        self.classes: List[str] = []

    def _tokenize(self, text: str) -> List[str]:
        words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
        stopwords = {
            "the", "and", "for", "with", "this", "that", "from", "near",
            "are", "has", "have", "had", "was", "were", "been", "being"
        }
        return [w for w in words if w not in stopwords]

    def fit(self, texts: List[str], labels: List[str]) -> None:
        self.doc_count = len(texts)
        self.classes = sorted(list(set(labels)))
        doc_freq: Dict[str, int] = defaultdict(int)

        tokenized_docs = []
        for text, label in zip(texts, labels):
            tokens = self._tokenize(text)
            tokenized_docs.append((tokens, label))
            self.class_doc_counts[label] += 1
            for term in set(tokens):
                doc_freq[term] += 1

        for term, df in doc_freq.items():
            self.idf[term] = math.log((1 + self.doc_count) / (1 + df)) + 1.0

        for tokens, label in tokenized_docs:
            term_counts = Counter(tokens)
            total = len(tokens) or 1
            for term, count in term_counts.items():
                tf = count / total
                weight = tf * self.idf.get(term, 1.0)
                self.class_word_counts[label][term] += weight
                self.class_total_words[label] += weight

    def predict_proba(self, text: str) -> Dict[str, float]:
        tokens = self._tokenize(text)
        if not tokens:
            return {c: 1.0 / len(self.classes) for c in self.classes}

        scores: Dict[str, float] = {}
        vocab_size = max(len(self.idf), 1)

        for c in self.classes:
            prior = math.log((self.class_doc_counts[c] + 1) / (self.doc_count + len(self.classes)))
            log_prob = prior
            denom = self.class_total_words[c] + vocab_size

            for term in tokens:
                num = self.class_word_counts[c].get(term, 0.0) + 1.0
                log_prob += math.log(num / denom)

            scores[c] = log_prob

        # Softmax normalization
        max_score = max(scores.values())
        exp_scores = {c: math.exp(s - max_score) for c, s in scores.items()}
        sum_exp = sum(exp_scores.values()) or 1.0
        return {c: exp_scores[c] / sum_exp for c in self.classes}


class LocalClassifier(AIEngine):
    """Offline classifier trained on synthetic Indian municipal grievances."""

    def __init__(self, dataset_path: Optional[Path] = None):
        self.dataset_path = dataset_path or DATASET_PATH
        self.vectorizer = None
        self.model = None
        self.pure_nb: Optional[PurePythonNaiveBayes] = None
        self.backend = "sklearn"
        self._is_trained = False
        self._train_model()

    def _train_model(self) -> None:
        """Train classifier on bundled dataset with sklearn or pure-Python fallback."""
        if not self.dataset_path.exists():
            raise FileNotFoundError(f"Training dataset not found at {self.dataset_path}")

        with open(self.dataset_path, "r", encoding="utf-8") as f:
            data: List[Dict[str, Any]] = json.load(f)

        texts = [item["text"] for item in data]
        labels = [item["category"] for item in data]

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.naive_bayes import MultinomialNB

            self.vectorizer = TfidfVectorizer(
                ngram_range=(1, 2),
                stop_words="english",
                max_features=1500
            )
            X = self.vectorizer.fit_transform(texts)
            self.model = MultinomialNB(alpha=0.5)
            self.model.fit(X, labels)
            self.backend = "sklearn:multinomial-nb"
        except Exception:
            # Fallback to pure Python if sklearn or C DLL is blocked
            self.pure_nb = PurePythonNaiveBayes()
            self.pure_nb.fit(texts, labels)
            self.backend = "pure-python:naive-bayes"

        self._is_trained = True

    def predict_category(self, text: str) -> Tuple[str, float]:
        """Predict grievance category and return (category, confidence)."""
        if not self._is_trained:
            self._train_model()

        clean_text = text.strip()
        if not clean_text:
            return "Uncategorised", 0.0

        if self.backend.startswith("sklearn") and self.model is not None and self.vectorizer is not None:
            X_input = self.vectorizer.transform([clean_text])
            probabilities = self.model.predict_proba(X_input)[0]
            max_idx = int(probabilities.argmax())
            category = self.model.classes_[max_idx]
            confidence = float(probabilities[max_idx])
        else:
            if self.pure_nb is None:
                self._train_model()
            probas = self.pure_nb.predict_proba(clean_text)  # type: ignore
            category = max(probas.items(), key=lambda x: x[1])[0]
            confidence = float(probas[category])

        if confidence < 0.35:
            return "Uncategorised", round(confidence, 2)

        return str(category), round(confidence, 2)

    def extract_location(self, text: str) -> Optional[str]:
        """Extract physical landmarks, ward, street, or sector from text."""
        patterns = [
            r"(?:near|outside|opposite|opp\.|behind|infront of|at|along)\s+([A-Za-z0-9\s,\-\.]{3,40}?)(?=[,\.\n;]|$)",
            r"(Ward\s+\d+(?:,\s+[A-Za-z\s]+(?:\s+(?:Cross|Main|Block|Lane|Stage|Nagar|Colony|Vihar))?)?)",
            r"(Sector\s+\d+(?:[,\s]+[A-Za-z0-9\s]+(?:\s+(?:Market|Chowk|Park|Center))?)?)",
            r"([A-Za-z0-9\s]+(?:Road|Marg|Chowk|Flyover|Circle|Junction|Bridge|Bypass))",
            r"(Gali\s+No\.?\s*\d+(?:[,\s]+[A-Za-z\s]+)?)",
            r"([A-Za-z\s]+(?:\s+(?:Nagar|Colony|Vihar|Enclave|Layout|Bhavan|Puram)))"
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                loc = match.group(1).strip()
                if len(loc) > 3 and not loc.lower().startswith(("this", "the road", "the street", "my house", "our area")):
                    return loc.rstrip(" ,.-")

        return None

    def extract_urgency(self, text: str) -> str:
        """Heuristically assess urgency based on safety hazard indicators."""
        text_lower = text.lower()

        for kw in HIGH_URGENCY_KEYWORDS:
            if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                return "HIGH"

        for kw in LOW_URGENCY_KEYWORDS:
            if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                return "LOW"

        return "MEDIUM"

    def draft_reply(self, category: str, location: Optional[str], urgency: str) -> str:
        """Generate a polite, empathetic municipal response draft for officer review."""
        loc_str = f" in the vicinity of {location}" if location else " at the reported location"
        sla = SLA_HOURS.get(category, 72)

        if urgency == "HIGH":
            urgency_note = " Given the urgent safety nature of this report, our emergency field crew has been notified on high priority."
        else:
            urgency_note = ""

        return (
            f"Dear Citizen, thank you for contacting the Municipal Corporation. "
            f"Your grievance regarding {category}{loc_str} has been registered under our standard "
            f"{sla}-hour service level agreement (SLA).{urgency_note} "
            f"An assigned municipal officer is inspecting the issue to initiate resolution."
        )

    def suggest(self, complaint_text: str) -> SuggestionPayload:
        """Produce complete suggestion payload using local ML and heuristic extraction."""
        category, confidence = self.predict_category(complaint_text)
        location = self.extract_location(complaint_text)
        urgency = self.extract_urgency(complaint_text)
        reply = self.draft_reply(category, location, urgency)

        return SuggestionPayload(
            category=category,
            confidence=confidence,
            location=location,
            urgency=urgency,  # type: ignore
            draft_reply=reply,
            source="local_fallback",
            raw_details={
                "extracted_location": location,
                "urgency_assessed": urgency,
                "model": self.backend
            }
        )
