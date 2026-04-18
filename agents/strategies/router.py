"""
DARA — Specialist Fix Strategy Router (Week 11-12)
====================================================
Each of the 8 error classes gets a domain-specific fix strategy with
class-appropriate Chain-of-Thought prompts instead of one generic template.

Error classes:
  null_reference     → NullReferenceStrategy
  type_mismatch      → TypeMismatchStrategy
  network_timeout    → NetworkTimeoutStrategy
  database_error     → DatabaseErrorStrategy
  authentication     → AuthenticationStrategy
  logic_error        → LogicErrorStrategy  (default / fallback)
  resource_leak      → ResourceLeakStrategy
  concurrency        → ConcurrencyStrategy

Each strategy:
  - generate_fix_prompt(bundle, root_cause, error) → str
  - confidence_boost(root_cause, bundle) → float   (0.0–0.3 additive)
  - display_name → str
"""
from __future__ import annotations

from abc import ABC, abstractmethod


# ── Base Strategy ─────────────────────────────────────────────

class BaseFixStrategy(ABC):
    display_name: str = "base"

    @abstractmethod
    def generate_fix_prompt(self, bundle, root_cause, error: dict) -> str:
        """Return a class-specific fix generation prompt."""

    def confidence_boost(self, root_cause, bundle) -> float:
        """
        Return an additive confidence boost (0.0–0.3) when the strategy
        is highly applicable to the current bundle.
        """
        return 0.0

    def _base_context(self, bundle, root_cause, error: dict) -> str:
        """Common context block used by all strategies."""
        return (
            f"Error class : {error.get('error_class', 'unknown')}\n"
            f"Service     : {error.get('service', 'unknown')}\n"
            f"Message     : {error.get('message', '')[:300]}\n"
            f"File        : {bundle.erroring_file or 'unknown'}\n"
            f"Function    : {bundle.erroring_function or 'unknown'}\n"
            f"Root cause  : {root_cause.root_cause}\n"
            f"Strategy    : {root_cause.suggested_strategy}\n"
            f"Confidence  : {root_cause.confidence:.2f}\n\n"
            f"Erroring code:\n```\n{(bundle.erroring_code or 'N/A')[:1500]}\n```"
        )


# ── Specialist Strategies ─────────────────────────────────────

class NullReferenceStrategy(BaseFixStrategy):
    display_name = "null_reference"

    def generate_fix_prompt(self, bundle, root_cause, error: dict) -> str:
        ctx = self._base_context(bundle, root_cause, error)
        return f"""You are fixing a NullPointerException / AttributeError / NullReferenceException.

{ctx}

Chain-of-thought approach for null reference fixes:
1. Identify the exact variable/attribute that is None/null at the error line
2. Determine WHERE it becomes None: missing initialisation, unexpected API return, or race condition
3. Choose the correct fix:
   a) Guard clause: if value is None → return early / raise descriptive error
   b) Default value: use `or default` / `?? default` / `getOrElse`
   c) Fix the upstream function that returns None unexpectedly
   d) Optional chaining if language supports it
4. Add a descriptive error message so future debuggers understand the intent
5. Do NOT swallow the None silently — either handle or propagate clearly

Output the COMPLETE fixed file content followed by:
{{"fixed_code": "<complete file>", "fix_explanation": "<what was null and why>", "null_variable": "<name>", "fix_type": "guard|default|upstream|optional"}}"""

    def confidence_boost(self, root_cause, bundle) -> float:
        code = bundle.erroring_code or ""
        if any(kw in code for kw in ["None", "null", "NullPointer", "NoneType", "is None"]):
            return 0.15
        return 0.0


class TypeMismatchStrategy(BaseFixStrategy):
    display_name = "type_mismatch"

    def generate_fix_prompt(self, bundle, root_cause, error: dict) -> str:
        ctx = self._base_context(bundle, root_cause, error)
        return f"""You are fixing a TypeError / ClassCastException / type mismatch.

{ctx}

Chain-of-thought approach for type mismatch fixes:
1. Identify the exact types involved (expected vs actual)
2. Trace where the wrong type originated (API, DB, config, user input)
3. Fix strategy:
   a) Type coercion at the boundary: int(), str(), float(), Type(value)
   b) Update function signature / type hint to reflect correct contract
   c) Add runtime validation with clear error at the entry point
   d) Update serialiser/deserialiser to output the correct type
4. Add or update type hints to prevent future regressions
5. Consider using Pydantic / dataclass with type enforcement

Output the COMPLETE fixed file then:
{{"fixed_code": "<complete file>", "fix_explanation": "<type details>", "expected_type": "<type>", "actual_type": "<type>", "fix_location": "boundary|origin|callsite"}}"""

    def confidence_boost(self, root_cause, bundle) -> float:
        msg = (bundle.erroring_code or "") + root_cause.root_cause
        if any(kw in msg for kw in ["TypeError", "int()", "str()", "cannot convert", "expected str"]):
            return 0.12
        return 0.0


class NetworkTimeoutStrategy(BaseFixStrategy):
    display_name = "network_timeout"

    def generate_fix_prompt(self, bundle, root_cause, error: dict) -> str:
        ctx = self._base_context(bundle, root_cause, error)
        return f"""You are fixing a network timeout / connection refused / read timeout error.

{ctx}

Chain-of-thought approach for network timeout fixes:
1. Identify the network call (HTTP, gRPC, DB connection, Redis, etc.)
2. Determine root cause: no timeout set, timeout too low, service unavailable, no retry
3. Fix strategy:
   a) Add explicit timeout parameter (never rely on defaults)
   b) Implement exponential backoff retry with jitter (3 retries max)
   c) Add circuit breaker pattern for repeated failures
   d) Return cached/stale data when upstream is down (graceful degradation)
   e) If DB: check connection pool size, implement connection timeout
4. Add structured logging: log the hostname, timeout value, retry attempt
5. Set timeout = 10s as default if none exists; use environment variable

Output the COMPLETE fixed file then:
{{"fixed_code": "<complete file>", "fix_explanation": "<what timed out>", "timeout_added_ms": <number>, "retry_attempts": <number>, "circuit_breaker": true/false}}"""

    def confidence_boost(self, root_cause, bundle) -> float:
        msg = root_cause.root_cause + (bundle.erroring_code or "")
        if any(kw in msg for kw in ["timeout", "connection", "refused", "unreachable", "httpx", "requests"]):
            return 0.20
        return 0.0


class DatabaseErrorStrategy(BaseFixStrategy):
    display_name = "database_error"

    def generate_fix_prompt(self, bundle, root_cause, error: dict) -> str:
        ctx = self._base_context(bundle, root_cause, error)
        return f"""You are fixing a database error (IntegrityError, OperationalError, constraint violation, deadlock).

{ctx}

Chain-of-thought approach for database error fixes:
1. Identify the exact DB operation (INSERT/UPDATE/DELETE/SELECT) that failed
2. Categorise: constraint violation | connection issue | query timeout | deadlock
3. Fix strategies:
   CONSTRAINT VIOLATION:
   a) Add pre-check (SELECT before INSERT) or use INSERT ... ON CONFLICT DO UPDATE
   b) Validate data BEFORE hitting the DB (Pydantic, schema check)
   c) Use unique_together or db-level constraint fix in migration
   CONNECTION / TIMEOUT:
   d) Add connection pool configuration and retry logic
   e) Check SQLAlchemy pool_pre_ping=True
   DEADLOCK:
   f) Reorder lock acquisition to be consistent across all code paths
   g) Use SELECT FOR UPDATE SKIP LOCKED for queue-style patterns
4. Wrap in proper try/except with specific exception types, not bare except
5. Add transaction rollback in the except block

Output the COMPLETE fixed file then:
{{"fixed_code": "<complete file>", "fix_explanation": "<DB error type and fix>", "db_operation": "INSERT|UPDATE|SELECT|DELETE", "error_subtype": "constraint|connection|deadlock|timeout"}}"""

    def confidence_boost(self, root_cause, bundle) -> float:
        msg = root_cause.root_cause + (bundle.erroring_code or "")
        if any(kw in msg for kw in ["IntegrityError", "OperationalError", "deadlock", "constraint", "postgres", "sqlalchemy"]):
            return 0.18
        return 0.0


class AuthenticationStrategy(BaseFixStrategy):
    display_name = "authentication"

    def generate_fix_prompt(self, bundle, root_cause, error: dict) -> str:
        ctx = self._base_context(bundle, root_cause, error)
        return f"""You are fixing an authentication or authorization error (401/403, token expired, permission denied).

{ctx}

Chain-of-thought approach for auth fixes:
1. Distinguish: Authentication (who are you?) vs Authorization (what can you do?)
2. Token/session issues:
   a) Expired token → add refresh token flow or return 401 with WWW-Authenticate
   b) Missing token → add guard at API boundary, document required header
   c) Invalid token → validate signature, check expiry, handle clock skew
3. Permission issues:
   a) Add role check before protected action
   b) Return 403 with descriptive error: "Requires admin role"
   c) Never expose whether resource exists (use 403 not 404 for protected resources)
4. DO NOT log tokens or secrets in error messages
5. Add token refresh logic if using JWT with short expiry

Output the COMPLETE fixed file then:
{{"fixed_code": "<complete file>", "fix_explanation": "<auth issue>", "auth_type": "authn|authz", "fix_type": "token_refresh|role_check|guard|validation"}}"""

    def confidence_boost(self, root_cause, bundle) -> float:
        msg = root_cause.root_cause
        if any(kw in msg for kw in ["401", "403", "token", "permission", "unauthorized", "forbidden"]):
            return 0.15
        return 0.0


class ResourceLeakStrategy(BaseFixStrategy):
    display_name = "resource_leak"

    def generate_fix_prompt(self, bundle, root_cause, error: dict) -> str:
        ctx = self._base_context(bundle, root_cause, error)
        return f"""You are fixing a resource leak (file handles, DB connections, sockets, memory).

{ctx}

Chain-of-thought approach for resource leak fixes:
1. Identify the resource type: file | DB connection | HTTP client | socket | thread
2. Find where the resource is opened/acquired
3. Find the missing close/release:
   a) Use `with` statement / context manager (Python)
   b) Use try/finally to guarantee close()
   c) Use asynccontextmanager for async resources
   d) Ensure connection pool is released (SQLAlchemy session.close())
4. Check for early returns or exceptions that bypass the cleanup
5. For HTTP clients (httpx, requests): use `async with httpx.AsyncClient() as c:` pattern

Output the COMPLETE fixed file then:
{{"fixed_code": "<complete file>", "fix_explanation": "<what leaked and how fixed>", "resource_type": "file|db|http|socket|thread", "fix_pattern": "context_manager|try_finally|pool_release"}}"""

    def confidence_boost(self, root_cause, bundle) -> float:
        code = bundle.erroring_code or ""
        if any(kw in code for kw in ["open(", "connect(", "AsyncClient", ".session", "acquire"]):
            return 0.12
        return 0.0


class ConcurrencyStrategy(BaseFixStrategy):
    display_name = "concurrency"

    def generate_fix_prompt(self, bundle, root_cause, error: dict) -> str:
        ctx = self._base_context(bundle, root_cause, error)
        return f"""You are fixing a concurrency / race condition / thread safety error.

{ctx}

Chain-of-thought approach for concurrency fixes:
1. Identify the shared state: variable, cache, DB row, file, queue
2. Identify where multiple code paths modify it concurrently
3. Fix strategies:
   RACE CONDITION:
   a) asyncio.Lock() for async Python
   b) threading.Lock() for threads
   c) Database-level locking: SELECT FOR UPDATE
   d) Optimistic locking: version field + retry on conflict
   ASYNC ISSUES:
   e) Add `await` where missing (common cause of race in async code)
   f) Use asyncio.gather() properly instead of creating tasks without awaiting
   CACHE STAMPEDE:
   g) Add lock around cache miss → fetch → cache set (check-then-set)
4. Minimize lock scope — hold lock only during the critical section

Output COMPLETE fixed file then:
{{"fixed_code": "<complete file>", "fix_explanation": "<race condition details>", "shared_state": "<what was shared>", "fix_type": "lock|db_lock|optimistic|atomic"}}"""

    def confidence_boost(self, root_cause, bundle) -> float:
        msg = root_cause.root_cause + (bundle.erroring_code or "")
        if any(kw in msg for kw in ["race", "concurrent", "Lock", "asyncio", "thread", "deadlock"]):
            return 0.15
        return 0.0


class LogicErrorStrategy(BaseFixStrategy):
    """Fallback strategy for logic errors and unclassified errors."""
    display_name = "logic_error"

    def generate_fix_prompt(self, bundle, root_cause, error: dict) -> str:
        ctx = self._base_context(bundle, root_cause, error)
        return f"""You are fixing a logic error or incorrectly implemented business rule.

{ctx}

Chain-of-thought approach:
1. Understand the INTENT of the failing code (what should it do?)
2. Identify the logical gap between intent and implementation
3. Trace through the logic step-by-step to find where it diverges
4. Write the fix that correctly implements the intended behavior
5. Add a comment explaining the rule to prevent future regression

Output the COMPLETE fixed file then:
{{"fixed_code": "<complete file>", "fix_explanation": "<logic error and correct behavior>", "intent": "<what the code should do>", "bug": "<what it does instead>"}}"""


# ── Strategy Router ───────────────────────────────────────────

_STRATEGY_MAP: dict[str, type[BaseFixStrategy]] = {
    "null_reference": NullReferenceStrategy,
    "type_mismatch": TypeMismatchStrategy,
    "network_timeout": NetworkTimeoutStrategy,
    "database_error": DatabaseErrorStrategy,
    "authentication": AuthenticationStrategy,
    "resource_leak": ResourceLeakStrategy,
    "concurrency": ConcurrencyStrategy,
    "logic_error": LogicErrorStrategy,
}


class StrategyRouter:
    """Routes an error to the correct specialist fix strategy."""

    @staticmethod
    def get_strategy(error_class: str) -> BaseFixStrategy:
        """
        Returns the specialist strategy for the given error_class.
        Falls back to LogicErrorStrategy for unknown classes.
        """
        cls = _STRATEGY_MAP.get(error_class.lower().replace(" ", "_"), LogicErrorStrategy)
        return cls()

    @staticmethod
    def all_classes() -> list[str]:
        return list(_STRATEGY_MAP.keys())

    @staticmethod
    def get_strategy_with_boost(
        error_class: str, root_cause, bundle
    ) -> tuple[BaseFixStrategy, float]:
        """
        Returns (strategy, confidence_boost) for the given error.
        The boost is added to root_cause.confidence in the fixer.
        """
        strategy = StrategyRouter.get_strategy(error_class)
        boost = strategy.confidence_boost(root_cause, bundle)
        return strategy, boost
