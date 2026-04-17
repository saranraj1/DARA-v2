"""
DARA — Error Normalizer
Converts raw payloads from any source into the canonical NormalizedError format.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone


class ErrorNormalizer:
    """Produces a consistent NormalizedError dict regardless of input source."""

    # Known auto-fixable error classes
    AUTO_FIX_CLASSES = {
        "NullPointerException",
        "AttributeError",
        "KeyError",
        "ImportError",
        "ModuleNotFoundError",
        "TypeError",
        "IndexError",
        "ValueError",
    }

    def normalize(self, raw: dict, source: str) -> dict:
        """
        Normalize a raw payload into the canonical error dict.
        source: 'github_actions' | 'opentelemetry' | 'direct'
        """
        error_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        error_class = self._extract_error_class(raw)
        message = self._extract_message(raw)
        stack_trace = self._extract_stack_trace(raw)
        file_path, line_number, column_number = self._extract_location(raw, stack_trace)

        normalized = {
            "id": error_id,
            "error_class": error_class,
            "message": message,
            "stack_trace": stack_trace,
            "file_path": file_path,
            "line_number": line_number,
            "column_number": column_number,
            "service": raw.get("service") or raw.get("service_name"),
            "environment": raw.get("environment", "production"),
            "severity": self._infer_severity(error_class, raw),
            "source": source,
            "commit_sha": raw.get("commit_sha") or raw.get("sha"),
            "branch": raw.get("branch") or raw.get("ref", "").replace("refs/heads/", ""),
            "trace_id": raw.get("trace_id"),
            "auto_fix_eligible": error_class in self.AUTO_FIX_CLASSES,
            "raw_payload": raw,
            "created_at": now,
            "signature": self._build_signature(error_class, message, file_path),
        }
        return normalized

    # ─── Private Extractors ──────────────────────────────────

    def _extract_error_class(self, raw: dict) -> str:
        for key in ("error_class", "exception_type", "errorType", "type"):
            if raw.get(key):
                return str(raw[key])
        # Try to parse from exception string
        message = raw.get("message", raw.get("body", ""))
        match = re.search(r"([A-Z][a-zA-Z]*(?:Error|Exception|Warning|Fault))", str(message))
        return match.group(1) if match else "UnknownError"

    def _extract_message(self, raw: dict) -> str:
        for key in ("message", "error_message", "msg", "description", "body"):
            if raw.get(key):
                return str(raw[key])[:2000]  # cap at 2000 chars
        return "No message provided"

    def _extract_stack_trace(self, raw: dict) -> str | None:
        for key in ("stack_trace", "stacktrace", "traceback", "exception", "stack"):
            if raw.get(key):
                return str(raw[key])[:10000]  # cap at 10KB
        return None

    def _extract_location(
        self, raw: dict, stack_trace: str | None
    ) -> tuple[str | None, int | None, int | None]:
        # Prefer explicit fields
        file_path = raw.get("file_path") or raw.get("filename")
        line_number = raw.get("line_number") or raw.get("lineno")
        column_number = raw.get("column_number") or raw.get("col")

        if file_path and line_number:
            return str(file_path), int(line_number), int(column_number) if column_number else None

        # Parse from Python stack trace: '  File "path/to/file.py", line 42'
        if stack_trace:
            matches = re.findall(
                r'File "([^"]+)", line (\d+)', stack_trace
            )
            if matches:
                last_file, last_line = matches[-1]
                return last_file, int(last_line), None

        return None, None, None

    def _infer_severity(self, error_class: str, raw: dict) -> str:
        if raw.get("severity"):
            return str(raw["severity"]).lower()
        critical_classes = {"SecurityException", "OutOfMemoryError", "StackOverflowError"}
        high_classes = {"NullPointerException", "DatabaseException", "ConnectionError"}
        if error_class in critical_classes:
            return "critical"
        if error_class in high_classes:
            return "high"
        return "medium"

    def _build_signature(
        self, error_class: str, message: str, file_path: str | None
    ) -> str:
        """Build a stable signature for deduplication."""
        raw = f"{error_class}:{file_path or ''}:{message[:100]}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
