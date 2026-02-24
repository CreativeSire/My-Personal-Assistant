"""
Phase 2 end / Phase 3 bridge:
Local-vs-remote routing for routine decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re

try:
    import joblib  # type: ignore
except Exception:  # pragma: no cover
    joblib = None


@dataclass
class RoutingDecision:
    provider: str  # local | remote
    confidence: float
    reason: str
    intent_class: str


SOCIAL_PATTERNS = [
    r"^(hey|hi|hello|yo|sup|howdy|morning|good morning|good evening|good afternoon)\b",
    r"^(thanks|thank you|ty|cheers|appreciate it|got it|understood|noted|ok|okay|cool|great|perfect|sounds good|sure)\b",
    r"^(bye|goodbye|see you|later|cya|take care)\b",
    r"^(victor[,!.]?\s*)?(hey|hi|hello|yo|sup)\b",
    r"^how are you",
    r"^what'?s up",
    r"^what are you up to",
]

SIMPLE_PATTERNS = [
    r"^what(?:'s| is) the (time|date|day)\b",
    r"^what time is it",
    r"^what(?:'s| is) today",
    r"^set a? ?timer\b",
    r"^remind me\b",
    r"\bwho are you\b",
    r"\btell me about yourself\b",
    r"\bare you (an )?ai\b",
    r"\bare you chatgpt\b",
    r"\bwhat can you do\b",
    r"\bskills? (do you have|loaded)\b",
    r"\bmodel routing status\b",
    r"\bsystem diagnostic\b",
    r"\brecent tasks?\b",
    r"\bbitcoin\b",
    r"\bopenai news\b",
    r"\bai news\b",
    r"\bnews (for|about) nigeria\b",
]


class LocalInferenceRouter:
    def __init__(self, threshold: float = 0.7):
        self.threshold = float(os.getenv("LOCAL_ROUTER_THRESHOLD", str(threshold)))
        allow_raw = os.getenv(
            "LOCAL_ROUTER_ALLOWED_CLASSES",
            "ops_status,filesystem_routine,invoice_routine",
        )
        self.allowed_classes = {x.strip() for x in allow_raw.split(",") if x.strip()}
        self.model_path = os.getenv(
            "LOCAL_ROUTER_MODEL_PATH",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_store", "intent_classifier.pkl"),
        )
        self._clf = None
        self._vectorizer = None
        self._load_or_train()

    def _load_or_train(self) -> None:
        if joblib and os.path.exists(self.model_path):
            try:
                bundle = joblib.load(self.model_path)
                self._vectorizer = bundle.get("vectorizer")
                self._clf = bundle.get("classifier")
                return
            except Exception:
                self._vectorizer = None
                self._clf = None

        # Best-effort training; if deps or data missing, we keep heuristic fallback.
        try:
            self._train_classifier()
        except Exception:
            self._vectorizer = None
            self._clf = None

    def _train_classifier(self) -> None:
        """
        Trains a simple intent classifier.
        Source of labels is conservative and derived from stored workflow/task signals.
        """
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
            from sklearn.svm import LinearSVC  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"scikit-learn unavailable: {exc}")

        # Build a small supervised dataset from corrections metadata emitted by the critic auto-log.
        try:
            import sqlite3

            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_store", "victor_platform.db")
            if not os.path.exists(db_path):
                return
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT rejected_output, preferred_output, metadata_json
                    FROM corrections
                    ORDER BY ts_utc DESC
                    LIMIT 2000
                    """
                ).fetchall()
            finally:
                conn.close()
        except Exception:
            return

        texts: list[str] = []
        labels: list[str] = []
        for row in rows:
            meta_raw = row["metadata_json"] or "{}"
            try:
                import json

                meta = json.loads(meta_raw) if isinstance(meta_raw, str) else {}
            except Exception:
                meta = {}
            label = str(meta.get("intent_class") or "").strip()
            intent = str(meta.get("intent") or "").strip()
            if not label or not intent:
                continue
            texts.append(intent)
            labels.append(label)

        # If we don't have supervised samples, skip training and keep heuristics.
        if len(texts) < 20:
            return

        vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=8000)
        X = vectorizer.fit_transform(texts)
        clf = LinearSVC()
        clf.fit(X, labels)

        self._vectorizer = vectorizer
        self._clf = clf
        if joblib:
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            joblib.dump({"vectorizer": vectorizer, "classifier": clf}, self.model_path)

    def classify_intent(self, intent: str) -> tuple[str, float]:
        text = (intent or "").strip().lower()
        if self._clf is not None and self._vectorizer is not None:
            try:
                X = self._vectorizer.transform([text])
                pred = self._clf.predict(X)[0]
                # LinearSVC doesn't provide calibrated probabilities; use distance as a proxy.
                conf = 0.7
                try:
                    dist = float(self._clf.decision_function(X)[0])
                    conf = max(0.0, min(0.99, 0.5 + (abs(dist) / 4.0)))
                except Exception:
                    conf = 0.7
                return (str(pred), float(conf))
            except Exception:
                pass
        if any(k in text for k in ["status", "latest tasks", "task status", "health", "summary"]):
            return ("ops_status", 0.9)
        if any(k in text for k in ["list files", "show files", "read file", "open file"]):
            return ("filesystem_routine", 0.82)
        if any(k in text for k in ["invoice", "receipt", "batch"]):
            return ("invoice_routine", 0.74)
        return ("novel", 0.45)

    def classify_tier(self, text: str) -> str:
        """
        Returns 'SOCIAL', 'SIMPLE', or 'COMPLEX'.
        Called BEFORE the main pipeline. No LLM involved.
        """
        t = str(text or "").strip().lower()
        for pattern in SOCIAL_PATTERNS:
            if re.search(pattern, t):
                return "SOCIAL"
        for pattern in SIMPLE_PATTERNS:
            if re.search(pattern, t):
                return "SIMPLE"
        return "COMPLEX"

    def route(self, intent: str) -> RoutingDecision:
        intent_class, confidence = self.classify_intent(intent)
        if intent_class in self.allowed_classes and confidence >= self.threshold:
            return RoutingDecision(
                provider="local",
                confidence=confidence,
                reason="routine_intent_high_confidence",
                intent_class=intent_class,
            )
        return RoutingDecision(
            provider="remote",
            confidence=confidence,
            reason="novel_or_low_confidence",
            intent_class=intent_class,
        )
