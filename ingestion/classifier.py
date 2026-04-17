"""
DARA — Error Classifier
Routes errors to one of 8 classes with priority (P0-P3) assignment.
Uses rule-based classification first, falls back to LLM for unknown patterns.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.llm_router import LLMRouter

logger = logging.getLogger(__name__)

# 8 canonical error classes
ERROR_CLASSES = [
    "null_reference",    # None/null access, AttributeError on None
    "type_mismatch",     # TypeErrors, wrong type passed
    "import_missing",    # ImportError, ModuleNotFoundError
    "index_out_of_range",# IndexError, KeyError
    "network_timeout",   # Connection timeouts, DNS failures
    "database_error",    # SQL errors, constraint violations
    "authentication",    # Auth failures, permission denied
    "logic_error",       # Wrong business logic, algorithm bugs
]

# Rule-based routing: (regex pattern) -> (class, priority, auto_fix_eligible)
RULES: list[tuple[re.Pattern, str, str, bool]] = [
    (re.compile(r"NoneType|AttributeError.*None|null.*reference", re.I), "null_reference", "P2", True),
    (re.compile(r"TypeError|type.*mismatch|expected.*got", re.I), "type_mismatch", "P2", True),
    (re.compile(r"ImportError|ModuleNotFoundError|No module named", re.I), "import_missing", "P3", True),
    (re.compile(r"IndexError|KeyError|list.*index.*range", re.I), "index_out_of_range", "P2", True),
    (re.compile(r"timeout|connection.*refused|ConnectionError|ECONNREFUSED", re.I), "network_timeout", "P1", False),
    (re.compile(r"IntegrityError|OperationalError|ProgrammingError|psycopg2", re.I), "database_error", "P1", False),
    (re.compile(r"401|403|Unauthorized|Forbidden|PermissionDenied|AuthError", re.I), "authentication", "P0", False),
    (re.compile(r"security|injection|XSS|CSRF|overflow|traversal", re.I), "authentication", "P0", False),
]


class ErrorClassifier:
    """
    Classifies normalized errors into one of 8 classes.
    Priority (P0=critical → P3=low) determines response urgency.
    """

    def __init__(self, llm_router: "LLMRouter | None" = None) -> None:
        self._llm = llm_router

    async def classify(self, error: dict) -> dict:
        """
        Classify a normalized error dict.
        Returns augmented dict with: error_class, priority, auto_fix_eligible added.
        """
        combined_text = " ".join(filter(None, [
            error.get("error_class", ""),
            error.get("message", ""),
            error.get("stack_trace", "")[:500] if error.get("stack_trace") else "",
        ]))

        # Step 1: Rule-based (fast path — no LLM cost)
        for pattern, class_name, priority, auto_fix in RULES:
            if pattern.search(combined_text):
                logger.debug(
                    "Classified by rule",
                    extra={"class": class_name, "priority": priority},
                )
                return {
                    **error,
                    "error_class": class_name,
                    "priority": priority,
                    "auto_fix_eligible": auto_fix and error.get("auto_fix_eligible", False),
                    "classification_method": "rule_based",
                }

        # Step 2: LLM fallback (for unknown error patterns)
        if self._llm:
            return await self._llm_classify(error, combined_text)

        # Step 3: Default when no LLM is available
        logger.warning("Could not classify error, defaulting to logic_error")
        return {
            **error,
            "error_class": "logic_error",
            "priority": "P2",
            "auto_fix_eligible": False,
            "classification_method": "default",
        }

    async def _llm_classify(self, error: dict, combined_text: str) -> dict:
        prompt = f"""Classify this error into exactly one of these 8 classes:
{', '.join(ERROR_CLASSES)}

Error text:
{combined_text[:800]}

Respond in this exact JSON format only:
{{"error_class": "class_name", "priority": "P0|P1|P2|P3", "auto_fix_eligible": true|false, "reasoning": "brief reason"}}

P0=security/auth, P1=critical system, P2=standard, P3=minor"""

        try:
            raw = await self._llm.complete(
                prompt,
                system="You are a software error classifier. Output only valid JSON.",
                priority="low",
                temperature=0.0,
                max_tokens=100,
            )
            import json
            result = json.loads(raw.strip())
            # Validate class
            if result.get("error_class") not in ERROR_CLASSES:
                result["error_class"] = "logic_error"
            return {
                **error,
                "error_class": result["error_class"],
                "priority": result.get("priority", "P2"),
                "auto_fix_eligible": result.get("auto_fix_eligible", False),
                "classification_method": "llm",
            }
        except Exception as e:
            logger.error("LLM classification failed", extra={"error": str(e)})
            return {
                **error,
                "error_class": "logic_error",
                "priority": "P2",
                "auto_fix_eligible": False,
                "classification_method": "default",
            }
