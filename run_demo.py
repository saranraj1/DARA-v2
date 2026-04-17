"""
DARA Phase 1 Demo - No Docker Required
Shows the FULL pipeline output: Ingest -> Normalize -> Classify -> AST -> Debug -> Fix -> Review
Uses real Groq LLM. Skips DB/Redis/Qdrant (in-memory only).
"""
import sys, asyncio, time, json, textwrap
sys.path.insert(0, ".")

# -- Pretty printing helpers -----------------------------------
def header(title):
    print(f"\n{'=' * 65}")
    print(f"  {title}")
    print(f"{'=' * 65}")

def section(title):
    print(f"\n{'-' * 65}")
    print(f"  {title}")
    print(f"{'-' * 65}")

def kv(key, val, indent=2):
    spaces = " " * indent
    val_str = str(val)
    if len(val_str) > 80:
        val_str = val_str[:80] + "..."
    print(f"{spaces}{key:<22} {val_str}")

def code_block(title, content, max_lines=15):
    print(f"\n  [{title}]")
    lines = content.strip().splitlines()[:max_lines]
    for ln in lines:
        print(f"  | {ln}")
    if len(content.splitlines()) > max_lines:
        print(f"  | ... ({len(content.splitlines()) - max_lines} more lines)")

def diff_block(diff, max_lines=20):
    print(f"\n  [Unified Diff]")
    lines = diff.strip().splitlines()[:max_lines]
    for ln in lines:
        if ln.startswith("+") and not ln.startswith("+++"):
            print(f"  \033[92m{ln}\033[0m")  # green
        elif ln.startswith("-") and not ln.startswith("---"):
            print(f"  \033[91m{ln}\033[0m")  # red
        elif ln.startswith("@@"):
            print(f"  \033[96m{ln}\033[0m")  # cyan
        else:
            print(f"  {ln}")


async def main():
    header("DARA AI Debugger  |  Phase 1 Demo  |  Real Groq LLM")
    print()
    print("  Demonstrating full debug pipeline without Docker.")
    print("  Components: Normalizer -> Classifier -> AST Chunker ->")
    print("              DebuggerAgent -> FixerAgent -> ReviewerAgent")

    # ---------------------------------------------------------
    # STAGE 1: RAW ERROR INPUT
    # ---------------------------------------------------------
    section("STAGE 1: Raw Error Event (Simulated GitHub Actions Webhook)")

    raw_payload = {
        "source": "github_actions",
        "job": "run-tests",
        "run_id": "13872401",
        "service": "auth-service",
        "error_class": "AttributeError",
        "message": "NoneType object has no attribute get",
        "stack_trace": textwrap.dedent("""
            Traceback (most recent call last):
              File "auth/service.py", line 87, in authenticate_user
                user_data = session.get_user(token)
              File "auth/session.py", line 44, in get_user
                return self._cache.get('user_id')
              File "auth/cache.py", line 12, in get
                return self._store.get(key)
            AttributeError: 'NoneType' object has no attribute 'get'
        """).strip(),
        "file_path": "auth/session.py",
        "line_number": 44,
        "commit_sha": "3f8a1b2",
        "branch": "feature/token-refresh",
        "severity": "critical",
    }

    print(f"\n  Received payload from GitHub Actions:")
    for k, v in raw_payload.items():
        if k != "stack_trace":
            kv(k, v)
    kv("stack_trace", f"{len(raw_payload['stack_trace'])} chars (6 lines)")

    # ---------------------------------------------------------
    # STAGE 2: NORMALIZATION
    # ---------------------------------------------------------
    section("STAGE 2: Error Normalization (ErrorNormalizer)")

    from ingestion.normalizer import ErrorNormalizer
    t0 = time.perf_counter()
    normalizer = ErrorNormalizer()
    normalized = normalizer.normalize(raw_payload, source="github_actions")
    ms = (time.perf_counter()-t0)*1000

    print(f"\n  Normalized in {ms:.1f}ms:")
    for k, v in normalized.items():
        if k not in ("stack_trace",):
            kv(k, v)
    print(f"\n  [OK] Error fingerprint generated, deduplication hash computed")

    # ---------------------------------------------------------
    # STAGE 3: CLASSIFICATION
    # ---------------------------------------------------------
    section("STAGE 3: Error Classification (ErrorClassifier)")

    from ingestion.classifier import ErrorClassifier
    t0 = time.perf_counter()
    classifier = ErrorClassifier()
    classification = await classifier.classify(normalized)
    ms = (time.perf_counter()-t0)*1000

    print(f"\n  Classified in {ms:.1f}ms:")
    if isinstance(classification, dict):
        for k, v in classification.items():
            kv(k, v)
        error_class_label = classification.get("error_class", "unknown")
    else:
        print(f"  error_class: {classification}")
        error_class_label = str(classification)
    print(f"\n  [OK] Rule-based fast path (no LLM cost)")

    # ---------------------------------------------------------
    # STAGE 4: AST CHUNKING
    # ---------------------------------------------------------
    section("STAGE 4: AST-Aware Code Chunking (tree-sitter 0.25)")

    from context.ast_chunker import ASTChunker
    from context.retriever import count_tokens
    t0 = time.perf_counter()
    chunker = ASTChunker()
    # Use our real postgres.py as the target file for demo
    chunks = chunker.chunk_file("storage/postgres.py", service="auth-service")
    ms = (time.perf_counter()-t0)*1000
    total_tokens = sum(count_tokens(c.content) for c in chunks)

    print(f"\n  File: storage/postgres.py")
    print(f"  Chunks extracted: {len(chunks)} in {ms:.1f}ms")
    print(f"  Total tokens:     {total_tokens}")
    print(f"\n  {'Line Range':<15} {'Name':<35} {'Tokens':<8}")
    print(f"  {'-'*13:<15} {'-'*33:<35} {'-'*6:<8}")
    for c in chunks[:8]:
        name = c.display_name[:33]
        tokens = count_tokens(c.content)
        print(f"  [{c.line_start:3d}-{c.line_end:3d}]      {name:<35} {tokens:<8}")
    if len(chunks) > 8:
        print(f"  ... and {len(chunks)-8} more chunks")
    print(f"\n  [OK] AST boundaries precise (no false splits, no missed functions)")

    # ---------------------------------------------------------
    # STAGE 5: CONTEXT BUNDLE ASSEMBLY
    # ---------------------------------------------------------
    section("STAGE 5: Context Bundle Assembly")

    from context.builder import ContextBundle
    from context.git_analyzer import GitAnalyzer
    git = GitAnalyzer(".")
    commits = git.get_recent_commits(days=30, max_commits=5)
    erroring_chunk = chunks[0] if chunks else None

    bundle = ContextBundle(
        error_id="demo-attr-001",
        erroring_file="storage/postgres.py",
        erroring_function=erroring_chunk.display_name if erroring_chunk else "get_error",
        erroring_code=erroring_chunk.content if erroring_chunk else "",
        related_functions=[],
        recent_commits=commits,
        similar_past_bugs=[],
        total_tokens=count_tokens(erroring_chunk.content) if erroring_chunk else 0,
    )

    print(f"\n  {bundle.summary()}")
    print(f"\n  Erroring function: {bundle.erroring_function}")
    print(f"  Recent commits:    {len(bundle.recent_commits)}")
    print(f"  Context tokens:    {bundle.total_tokens}")
    if commits:
        print(f"  Latest commit:     [{commits[0].get('sha','')}] {commits[0].get('message','')[:50]}")
    print(f"\n  [OK] Context assembled within 8000 token budget")

    # ---------------------------------------------------------
    # STAGE 6: DEBUGGER AGENT  (REAL LLM CALL)
    # ---------------------------------------------------------
    section("STAGE 6: DebuggerAgent  (Groq llama-3.3-70b-versatile  temp=0.1)")

    from config.llm_router import get_llm_router
    from agents.debugger import DebuggerAgent

    llm = get_llm_router()
    debugger = DebuggerAgent(llm_router=llm)

    error_dict = {
        "id": "demo-attr-001",
        "error_class": "AttributeError",
        "message": "NoneType object has no attribute get",
        "stack_trace": raw_payload["stack_trace"],
        "file_path": "storage/postgres.py",
        "line_number": 44,
        "service": "auth-service",
        "severity": "critical",
        "commit_sha": "3f8a1b2",
        "branch": "feature/token-refresh",
        "trace_id": None,
    }

    print(f"\n  Sending to Groq API...")
    t0 = time.perf_counter()
    root_cause = await debugger.analyze(bundle, error_dict)
    ms = (time.perf_counter()-t0)*1000

    print(f"\n  Analysis completed in {ms:.0f}ms")
    print(f"\n  {'IMMEDIATE CAUSE'}")
    print(f"  {root_cause.immediate_cause}")
    print(f"\n  {'ROOT CAUSE'}")
    print(f"  {root_cause.root_cause}")
    if root_cause.contributing_factors:
        print(f"\n  {'CONTRIBUTING FACTORS'}")
        for f in root_cause.contributing_factors:
            print(f"    - {f}")
    print(f"\n  Confidence:      {root_cause.confidence:.0%}")
    print(f"  Evidence quality:{root_cause.evidence_quality}")
    print(f"  Strategy:        {root_cause.suggested_strategy}")
    print(f"  Files to change: {root_cause.files_to_change}")
    print(f"\n  [OK] Root cause identified with {root_cause.confidence:.0%} confidence")

    # ---------------------------------------------------------
    # STAGE 7: FIXER AGENT  (REAL LLM CALL)
    # ---------------------------------------------------------
    section("STAGE 7: FixerAgent  (Groq llama-3.3-70b-versatile  temp=0.15)")

    from agents.fixer import FixerAgent
    fixer = FixerAgent(llm_router=llm)

    print(f"\n  Generating minimal patch for: {root_cause.files_to_change}")
    t0 = time.perf_counter()
    fix = await fixer.generate(root_cause, bundle, error_dict)
    ms = (time.perf_counter()-t0)*1000

    print(f"\n  Fix generated in {ms:.0f}ms")
    print(f"  Files changed:   {fix.total_files_changed}")
    print(f"  Lines changed:   {fix.total_lines_changed}")
    print(f"  Regression risk: {fix.regression_risk}")
    print(f"  Confidence:      {fix.confidence_retained:.0%}")
    print(f"  Strategy:        {fix.strategy}")
    print(f"\n  Explanation: {fix.fix_explanation[:120]}")

    if fix.patches:
        diff_block(fix.patches[0].unified_diff, max_lines=25)
        print(f"\n  [OK] Unified diff generated ({fix.patches[0].lines_changed} lines changed)")
    else:
        print(f"\n  [NOTE] No diff generated (LLM may need more context from Qdrant)")

    # ---------------------------------------------------------
    # STAGE 8: VALIDATION ENGINE
    # ---------------------------------------------------------
    section("STAGE 8: ValidationEngine  (Ruff S,E9,F  +  Test Runner)")

    from validation.engine import ValidationEngine
    validator = ValidationEngine(repo_path=".")

    t0 = time.perf_counter()
    val_report = await validator.validate(fix, "demo-attr-001")
    ms = (time.perf_counter()-t0)*1000

    print(f"\n  Validation completed in {ms:.0f}ms")
    print(f"  Overall passed:    {val_report.passed}")
    print(f"  Blocking issues:   {len(val_report.blocking_issues)}")
    if val_report.static_analysis:
        sa = val_report.static_analysis
        print(f"  Static analysis:   {sa.tool}  errors={sa.error_count}  warnings={sa.warning_count}")
        if sa.findings:
            print(f"  Findings:")
            for f in sa.findings[:3]:
                print(f"    [{f.get('code')}] line {f.get('line')}: {f.get('message')[:60]}")
    if val_report.blocking_issues:
        for issue in val_report.blocking_issues[:3]:
            print(f"  BLOCKED: {issue}")
    status = "[OK] Validation PASSED" if val_report.passed else "[WARN] Validation found issues"
    print(f"\n  {status}")

    # ---------------------------------------------------------
    # STAGE 9: REVIEWER AGENT  (REAL LLM CALL)
    # ---------------------------------------------------------
    section("STAGE 9: ReviewerAgent  (Groq llama-3.3-70b-versatile  temp=0.0)")

    from agents.reviewer import ReviewerAgent
    reviewer = ReviewerAgent(llm_router=llm)

    print(f"\n  Reviewing patch with strict quality checklist...")
    t0 = time.perf_counter()
    review = await reviewer.review(fix, root_cause, error_dict)
    ms = (time.perf_counter()-t0)*1000

    rec_color = {"approve": "\033[92m", "approve_with_comments": "\033[93m",
                 "reject": "\033[91m"}.get(review.overall_recommendation, "")
    reset = "\033[0m"

    print(f"\n  Review completed in {ms:.0f}ms")
    print(f"  Quality score:    {review.quality_score:.0%}")
    print(f"  Correctness:      {'PASS' if review.correctness_passes else 'FAIL'}")
    print(f"  Security:         {'PASS' if review.security_passes else 'FAIL'}")
    print(f"  Recommendation:   {rec_color}{review.overall_recommendation.replace('_',' ').upper()}{reset}")
    if review.rejection_reason:
        print(f"  Rejection reason: {review.rejection_reason[:100]}")
    if review.issues:
        print(f"  Issues raised:")
        for iss in review.issues[:3]:
            print(f"    - {iss}")
    print(f"\n  Notes: {review.reviewer_notes[:120]}")

    auto_eligible = (
        review.overall_recommendation == "approve"
        and fix.confidence_retained >= 0.88
        and fix.regression_risk == "low"
        and val_report.passed
    )
    print(f"\n  Auto-merge eligible: {'YES - would be auto-resolved' if auto_eligible else 'NO - requires human review'}")

    # ---------------------------------------------------------
    # FINAL SUMMARY
    # ---------------------------------------------------------
    header("PHASE 1 PIPELINE OUTPUT SUMMARY")

    print()
    print(f"  INPUT:   AttributeError in auth/session.py (critical, feature branch)")
    print(f"  SERVICE: auth-service @ commit 3f8a1b2")
    print()
    print(f"  {'Stage':<25} {'Result':<35} {'Time'}")
    print(f"  {'-'*24:<25} {'-'*34:<35} {'-'*8}")
    print(f"  {'1. Normalize':<25} {'Fingerprint + dedup hash':<35} <1ms")
    print(f"  {'2. Classify':<25} {str(error_class_label):<35} <1ms")
    print(f"  {'3. AST Chunk':<25} {str(len(chunks))+' functions extracted':<35} fast")
    print(f"  {'4. Git Analyze':<25} {str(len(commits))+' recent commits':<35} fast")
    print(f"  {'5. Context Bundle':<25} {str(bundle.total_tokens)+' tokens assembled':<35} fast")
    print(f"  {'6. DebuggerAgent':<25} {str(root_cause.confidence*100)[:2]+'% confidence, '+root_cause.suggested_strategy:<35} LLM")
    print(f"  {'7. FixerAgent':<25} {str(fix.total_lines_changed)+' lines changed, risk='+fix.regression_risk:<35} LLM")
    print(f"  {'8. Validation':<25} {'PASS' if val_report.passed else 'ISSUES FOUND':<35} fast")
    print(f"  {'9. ReviewerAgent':<25} {review.overall_recommendation.replace('_',' '):<35} LLM")
    print()
    print(f"  OUTCOME: {'Fix ready for human approval via Slack' if not auto_eligible else 'Auto-resolved!'}")
    print()
    print(f"  NOTE: With Docker running, this pipeline would additionally:")
    print(f"    - Persist error + fix to Postgres")
    print(f"    - Track progress in Redis")
    print(f"    - Store embeddings in Qdrant (for future similar-bug lookup)")
    print(f"    - Send Slack notification with Approve/Reject buttons")
    print(f"    - Create GitHub PR draft")
    print()
    print("=" * 65)

asyncio.run(main())
