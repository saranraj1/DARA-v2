# DARA-v2 Empirical Failure Modes & Mitigations

This document outlines the four primary failure modes identified in autonomous multi-agent software repair, how DARA-v2 defends against them, and the remaining research boundaries.

---

## Failure Mode 1: Uncalibrated Confidence (Overconfidence on Broken Fixes)

### 1. Description & Root Cause
Neural language models trained on next-token prediction frequently output high self-reported confidence scores (e.g. `confidence: 0.95`) even when proposing incorrect, hallucinated, or semantically inverted logic. This occurs because token generation fluency does not correlate directly with computational correctness.

### 2. Manifestation in Autonomous Pipelines
An uncalibrated confidence score can cause an autonomous system to bypass human review (auto-merging bad code directly into `main`), leading to severe production outages.

### 3. DARA-v2 Multi-Layer Defenses
- **Multi-Agent Decoupling**: Confidence is evaluated independently by `DebuggerAgent`, retained and penalized by `FixerAgent` based on lines changed, and challenged by `ReviewerAgent`.
- **Multi-Predicate Gating**: Confidence score alone is NEVER sufficient for auto-merging. Auto-approval requires:
  $$\text{AutoApprove} = (\text{Review} == \text{approve}) \land (\text{Conf} \ge 0.88) \land (\text{Risk} == \text{low}) \land (\text{SandboxPassed} == \text{true})$$
- **Lower Bound Gating (< 0.35)**: Any confidence score $< 0.35$ immediately triggers `escalation_trigger="confidence_gate"` and aborts code generation.

### 4. Benchmark Fixture
- Benchmark Case `BM-007` (`UnresolvableSystemState`): Confirms that low-confidence opaque errors safely escalate without attempting destructive patches.

---

## Failure Mode 2: Outcome-Only Semantic Verification (Test Suite Blindspots)

### 1. Description & Root Cause
Software test suites often have incomplete branch coverage, heavy mocking, or assert-free execution paths. A patch may pass existing tests while introducing subtle semantic regressions in unexercised execution paths.

### 2. Manifestation in Autonomous Pipelines
A candidate fix passes sandbox pytest execution (`validation.passed = True`) and is perceived as verified, but introduces silent data corruption or performance degradation.

### 3. DARA-v2 Multi-Layer Defenses
- **Static Security & Linting Layer**: `StaticAnalyzer` (Ruff `E9`, `S`) and `SecurityAuditor` (Bandit + Semgrep) evaluate AST-level invariants independently of whether tests pass.
- **Blast Radius Analysis**: `BlastRadiusAnalyzer` traverses Neo4j service dependency topologies. If a function modification impacts multiple downstream consumers or shared API contracts, auto-merge is blocked regardless of local test passage.
- **Conservative Line/File Caps**: Auto-merge is strictly constrained to single-file patches ($\le 15$ lines by default). Multi-file or large patches require human review.

### 4. Benchmark Fixture
- Benchmark Case `BM-005` (SQL Injection): Tests pass, but `SecurityAuditor` intercepts and blocks the fix with `security_blocked`.
- Benchmark Case `BM-006` (Connection Pool): Tests pass, but `BlastRadiusAnalyzer` triggers `critical_blast_radius`.

---

## Failure Mode 3: Retrieval Degradation Out-of-Domain

### 1. Description & Root Cause
Dense vector retrieval (Qdrant semantic search) relies on embedding similarity. In unindexed repositories, newly introduced frameworks, or polyglot boundaries (e.g. Python calling Rust or C extensions), semantic similarity fails to retrieve the correct call-site context.

### 2. Manifestation in Autonomous Pipelines
`ContextBuilder` populates the context window with irrelevant code chunks, causing `DebuggerAgent` to hallucinate nonexistent functions or misidentify the faulting file.

### 3. DARA-v2 Multi-Layer Defenses
- **Hybrid Context Assembly**: Context is not solely dependent on vector search. `ContextBuilder` integrates deterministic AST parsing (`ASTChunker`), exact git blame attribution (`GitAnalyzer`), and explicit stack trace line boundaries.
- **Graceful Token Budgeting**: Retrieval queries operate within explicit token caps (3,000 tokens for related code, 1,500 tokens for historical bugs), ensuring the primary stack trace and erroring function always take priority.
- **Ablation Resiliency**: Verified in tests that disabling semantic retrieval (`enable_semantic_retrieval=False`) maintains graceful fallback to AST and Git context.

---

## Failure Mode 4: Pattern-Library Staleness

### 1. Description & Root Cause
Historical fix templates stored in `PatternMemory` (from prior successful resolutions) become stale when underlying libraries are upgraded, function signatures change, or APIs are deprecated.

### 2. Manifestation in Autonomous Pipelines
`PatternMemory` fast-path attempts to apply a previously successful template, producing syntax errors or deprecated parameter invocations.

### 3. DARA-v2 Multi-Layer Defenses
- **Success Rate Threshold ($\ge 0.85$)**: Templates are only selected if their historical win rate $\ge 85\%$.
- **Validation Engine Interception**: Even if a template is matched, any applied patch must pass compilation (`py_compile`) and test execution. If it fails, the pipeline discards the template and falls back to dynamic `DebuggerAgent` $\to$ `FixerAgent` generation.
- **Strategy Monitor Feedback**: `StrategyMonitor` continuously computes live win rates and automatically demotes underperforming templates.

### 4. Benchmark Fixture
- Benchmark Case `BM-008` (`TypeError` with deprecated parameter): Verifies detection and fallback handling for stale patterns.
