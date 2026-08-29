// Mock data layer for DARA. Swap api.ts to point to a real REST backend later.
import { subMinutes, subHours, subDays } from "date-fns";

export type Severity = "critical" | "high" | "medium" | "low";
export type ErrorStatus = "pending" | "analyzing" | "fixed" | "escalated" | "ignored";
export type FixOutcome = "pending" | "accepted" | "rejected" | "auto_merged";
export type Validation = "pass" | "fail" | "pending";

export interface ErrorRow {
  id: string;
  error_class: string;
  message: string;
  stack: string;
  service: string;
  repo: string;
  commit: string;
  pipeline_run_id: string;
  github_run_url: string;
  severity: Severity;
  status: ErrorStatus;
  created_at: string;
}

export interface FixRow {
  id: string;
  error_id: string;
  error_class: string;
  strategy: string;
  confidence: number;
  validation: Validation;
  outcome: FixOutcome;
  pr_url?: string;
  files_changed: string[];
  diff: string;
  explanation: string;
  validation_output: string;
  created_at: string;
}

export interface PatternRow {
  id: string;
  name: string;
  error_class: string;
  description: string;
  match_count: number;
  success_rate: number;
  threshold: number;
  last_matched: string;
  status: "active" | "deprecated";
}

export interface StrategyRow {
  id: string;
  error_class: string;
  variant_name: string;
  status: "draft" | "testing" | "active" | "retired";
  acceptance_rate: number;
  total_uses: number;
  success_rate: number;
  generated_by: "llm" | "human";
  created_at: string;
}

export interface ServiceNode {
  id: string;
  call_count: number;
  error_count: number;
  error_rate: number;
  avg_latency: number;
  last_seen: string;
}
export interface ServiceEdge { source: string; target: string; calls: number; error_rate: number; }

export interface TraceRow {
  id: string;
  span_count: number;
  services: string[];
  has_error: boolean;
  started_at: string;
  duration_ms: number;
}

export interface AuditRow {
  id: string;
  timestamp: string;
  action: "approved" | "rejected";
  fix_id: string;
  error_class: string;
  reviewer: string;
  comment?: string;
  confidence: number;
}

const services = [
  "checkout-api", "payments-svc", "inventory-svc", "auth-gateway",
  "user-profile", "notifications", "search-indexer", "recs-engine",
  "billing-worker", "webhook-router",
];
const errorClasses = ["null_reference", "timeout", "syntax_error", "type_mismatch", "deadlock", "rate_limit_exceeded", "schema_drift", "memory_leak"];
const strategies = ["llm_single_file", "llm_multi_file", "pattern_match", "rollback_diff", "test_first"];

function pick<T>(arr: T[], i: number): T { return arr[i % arr.length]; }
function rand(seed: number) { const x = Math.sin(seed) * 10000; return x - Math.floor(x); }

const sampleStack = `Traceback (most recent call last):
  File "/app/services/checkout/handler.py", line 142, in process_order
    total = order.line_items.sum(lambda li: li.price * li.qty)
  File "/app/lib/decimal_utils.py", line 28, in sum
    return functools.reduce(operator.add, items)
TypeError: unsupported operand type(s) for +: 'NoneType' and 'Decimal'`;

const sampleDiff = `diff --git a/services/checkout/handler.py b/services/checkout/handler.py
@@ -139,7 +139,9 @@ def process_order(order):
     if not order.line_items:
         raise EmptyOrderError(order.id)

-    total = order.line_items.sum(lambda li: li.price * li.qty)
+    total = order.line_items.sum(
+        lambda li: (li.price or Decimal("0")) * (li.qty or 0)
+    )
     return Receipt(order_id=order.id, total=total)`;

export const errors: ErrorRow[] = Array.from({ length: 47 }, (_, i) => {
  const sev: Severity[] = ["critical", "high", "medium", "low"];
  const status: ErrorStatus[] = ["pending", "analyzing", "fixed", "escalated", "ignored"];
  return {
    id: `err_${(1000 + i).toString(36)}`,
    error_class: pick(errorClasses, i),
    message: pick([
      "TypeError: unsupported operand type(s) for +: 'NoneType' and 'Decimal'",
      "TimeoutError: upstream did not respond within 5000ms",
      "SyntaxError: unexpected token '}' at line 88",
      "DeadlockDetected: row-level lock on payments.tx_log",
      "RateLimitExceeded: stripe.charges.create (100/s)",
      "SchemaDriftError: column 'tenant_id' missing on users",
    ], i),
    stack: sampleStack,
    service: pick(services, i * 3),
    repo: "acme/" + pick(["monolith", "checkout", "platform", "billing"], i),
    commit: Math.floor(rand(i + 1) * 0xfffffff).toString(16).padStart(7, "0"),
    pipeline_run_id: `run_${(2000 + i).toString(36)}`,
    github_run_url: "https://github.com/acme/monolith/actions/runs/" + (1000000 + i),
    severity: pick(sev, i + 1),
    status: pick(status, i),
    created_at: subMinutes(new Date(), i * 17 + 3).toISOString(),
  };
});

export const fixes: FixRow[] = Array.from({ length: 32 }, (_, i) => {
  const outcomes: FixOutcome[] = ["pending", "pending", "pending", "accepted", "rejected", "auto_merged"];
  const validations: Validation[] = ["pass", "pass", "fail", "pending"];
  return {
    id: `fix_${(7000 + i).toString(36)}`,
    error_id: errors[i % errors.length].id,
    error_class: errors[i % errors.length].error_class,
    strategy: pick(strategies, i),
    confidence: Math.round((0.42 + rand(i + 5) * 0.55) * 100),
    validation: pick(validations, i),
    outcome: pick(outcomes, i),
    pr_url: i % 3 === 0 ? `https://github.com/acme/monolith/pull/${1200 + i}` : undefined,
    files_changed: [
      `services/${pick(["checkout", "auth", "billing"], i)}/handler.py`,
      ...(i % 2 === 0 ? [`tests/test_${pick(["checkout", "auth"], i)}.py`] : []),
    ],
    diff: sampleDiff,
    explanation: "The handler assumed `price` and `qty` were always populated, but legacy line items written before migration 0042 can carry NULL values. Coercing both to safe defaults before multiplication preserves the existing contract while preventing the TypeError surfaced in production.",
    validation_output: `pytest -q
.....F....
1 failed, 9 passed in 2.41s
ruff: 0 errors, 0 warnings`,
    created_at: subMinutes(new Date(), i * 23 + 5).toISOString(),
  };
});

export const patterns: PatternRow[] = [
  { id: "pat_01", name: "Null-coalesce missing on Decimal math", error_class: "null_reference", description: "Detects arithmetic on optional Decimal fields without coalescing.", match_count: 142, success_rate: 87, threshold: 0.82, last_matched: subMinutes(new Date(), 12).toISOString(), status: "active" },
  { id: "pat_02", name: "Upstream timeout w/ no retry policy", error_class: "timeout", description: "HTTP client calls without exponential backoff or circuit breaker.", match_count: 88, success_rate: 73, threshold: 0.78, last_matched: subHours(new Date(), 2).toISOString(), status: "active" },
  { id: "pat_03", name: "Schema drift after migration", error_class: "schema_drift", description: "ORM model references columns absent in target DB.", match_count: 31, success_rate: 91, threshold: 0.85, last_matched: subHours(new Date(), 6).toISOString(), status: "active" },
  { id: "pat_04", name: "Stripe 429 on bulk charge", error_class: "rate_limit_exceeded", description: "Loop creates charges synchronously > 100/s.", match_count: 19, success_rate: 64, threshold: 0.7, last_matched: subDays(new Date(), 1).toISOString(), status: "active" },
  { id: "pat_05", name: "Deprecated: hardcoded UTC offset", error_class: "type_mismatch", description: "Replaced by tz-aware datetime pattern.", match_count: 7, success_rate: 42, threshold: 0.6, last_matched: subDays(new Date(), 14).toISOString(), status: "deprecated" },
];

export const strategiesData: StrategyRow[] = [
  { id: "stg_01", error_class: "null_reference", variant_name: "explain-then-patch v3", status: "active", acceptance_rate: 81, total_uses: 412, success_rate: 81, generated_by: "human", created_at: subDays(new Date(), 32).toISOString() },
  { id: "stg_02", error_class: "null_reference", variant_name: "diff-only minimal", status: "testing", acceptance_rate: 67, total_uses: 88, success_rate: 67, generated_by: "llm", created_at: subDays(new Date(), 6).toISOString() },
  { id: "stg_03", error_class: "timeout", variant_name: "retry-policy injector", status: "active", acceptance_rate: 74, total_uses: 201, success_rate: 74, generated_by: "human", created_at: subDays(new Date(), 21).toISOString() },
  { id: "stg_04", error_class: "timeout", variant_name: "circuit-break v2", status: "testing", acceptance_rate: 71, total_uses: 44, success_rate: 71, generated_by: "llm", created_at: subDays(new Date(), 4).toISOString() },
  { id: "stg_05", error_class: "schema_drift", variant_name: "migration-aware", status: "active", acceptance_rate: 89, total_uses: 67, success_rate: 89, generated_by: "human", created_at: subDays(new Date(), 18).toISOString() },
  { id: "stg_06", error_class: "deadlock", variant_name: "lock-order rewrite", status: "draft", acceptance_rate: 0, total_uses: 0, success_rate: 0, generated_by: "llm", created_at: subDays(new Date(), 1).toISOString() },
  { id: "stg_07", error_class: "rate_limit_exceeded", variant_name: "batched-async v1", status: "retired", acceptance_rate: 31, total_uses: 22, success_rate: 31, generated_by: "human", created_at: subDays(new Date(), 90).toISOString() },
];

export const topology: { nodes: ServiceNode[]; edges: ServiceEdge[] } = {
  nodes: services.map((id, i) => ({
    id,
    call_count: Math.round(800 + rand(i + 11) * 9000),
    error_count: Math.round(rand(i + 22) * 120),
    error_rate: +(rand(i + 33) * (i === 1 || i === 4 ? 12 : 4)).toFixed(2),
    avg_latency: Math.round(20 + rand(i + 44) * 380),
    last_seen: subMinutes(new Date(), Math.round(rand(i) * 10)).toISOString(),
  })),
  edges: [
    { source: "auth-gateway", target: "user-profile", calls: 4210, error_rate: 0.4 },
    { source: "auth-gateway", target: "checkout-api", calls: 3120, error_rate: 0.6 },
    { source: "checkout-api", target: "payments-svc", calls: 2890, error_rate: 6.1 },
    { source: "checkout-api", target: "inventory-svc", calls: 2640, error_rate: 1.2 },
    { source: "payments-svc", target: "billing-worker", calls: 1980, error_rate: 8.4 },
    { source: "checkout-api", target: "notifications", calls: 1450, error_rate: 0.8 },
    { source: "user-profile", target: "notifications", calls: 980, error_rate: 0.3 },
    { source: "search-indexer", target: "recs-engine", calls: 720, error_rate: 1.9 },
    { source: "recs-engine", target: "user-profile", calls: 510, error_rate: 0.5 },
    { source: "webhook-router", target: "billing-worker", calls: 410, error_rate: 2.1 },
    { source: "webhook-router", target: "notifications", calls: 380, error_rate: 0.9 },
    { source: "payments-svc", target: "webhook-router", calls: 320, error_rate: 1.0 },
  ],
};

export const traces: TraceRow[] = Array.from({ length: 14 }, (_, i) => ({
  id: `tr_${(0xabc000 + i).toString(16)}`,
  span_count: Math.round(4 + rand(i + 9) * 38),
  services: services.slice(i % 4, (i % 4) + 3 + (i % 3)),
  has_error: i % 4 === 0,
  started_at: subMinutes(new Date(), i * 6 + 2).toISOString(),
  duration_ms: Math.round(40 + rand(i + 2) * 2400),
}));

export const audit: AuditRow[] = Array.from({ length: 22 }, (_, i) => ({
  id: `aud_${i}`,
  timestamp: subHours(new Date(), i * 3 + 1).toISOString(),
  action: i % 3 === 0 ? "rejected" : "approved",
  fix_id: fixes[i % fixes.length].id,
  error_class: fixes[i % fixes.length].error_class,
  reviewer: pick(["alice@acme", "ben@acme", "carol@acme", "system"], i),
  comment: i % 4 === 0 ? "tested locally; LGTM" : i % 5 === 0 ? "reverts unrelated whitespace, please redo" : undefined,
  confidence: fixes[i % fixes.length].confidence,
}));

// ─── Synthetic live-tail generators ──────────────────────────────────────────
// Replace with real WebSocket/SSE subscriptions when wiring the backend.

const sampleMessages = [
  "TypeError: unsupported operand type(s) for +: 'NoneType' and 'Decimal'",
  "TimeoutError: upstream did not respond within 5000ms",
  "ConnectionResetError: peer closed connection during handshake",
  "DeadlockDetected: row-level lock on payments.tx_log",
  "RateLimitExceeded: stripe.charges.create (100/s)",
  "KeyError: 'tenant_id' missing from request context",
  "AssertionError: invariant violated in reconciliation loop",
  "SchemaDriftError: column 'archived_at' missing on orders",
];
let _liveErrCounter = 0;
let _liveFixCounter = 0;

export function makeLiveError(): ErrorRow {
  const i = _liveErrCounter++;
  const r = Math.random();
  const sev: Severity[] = ["critical", "high", "medium", "low"];
  const status: ErrorStatus[] = ["pending", "analyzing"];
  return {
    id: `err_live_${Date.now().toString(36)}_${i}`,
    error_class: pick(errorClasses, Math.floor(r * 100) + i),
    message: pick(sampleMessages, Math.floor(r * 100) + i),
    stack: sampleStack,
    service: pick(services, Math.floor(r * 100) + i),
    repo: "acme/" + pick(["monolith", "checkout", "platform", "billing"], i),
    commit: Math.floor(Math.random() * 0xfffffff).toString(16).padStart(7, "0"),
    pipeline_run_id: `run_live_${i}`,
    github_run_url: "https://github.com/acme/monolith/actions/runs/" + (9000000 + i),
    severity: pick(sev, Math.floor(r * 4)),
    status: pick(status, i),
    created_at: new Date().toISOString(),
  };
}

export function makeLiveFix(existingErrors: ErrorRow[] = errors): FixRow {
  const i = _liveFixCounter++;
  const r = Math.random();
  const target = existingErrors[Math.floor(r * existingErrors.length)] ?? errors[0];
  const validations: Validation[] = ["pass", "pass", "fail", "pending"];
  return {
    id: `fix_live_${Date.now().toString(36)}_${i}`,
    error_id: target.id,
    error_class: target.error_class,
    strategy: pick(strategies, Math.floor(r * 100) + i),
    confidence: Math.round(45 + Math.random() * 50),
    validation: pick(validations, Math.floor(r * 4)),
    outcome: "pending",
    pr_url: r > 0.6 ? `https://github.com/acme/monolith/pull/${1300 + i}` : undefined,
    files_changed: [
      `services/${pick(["checkout", "auth", "billing"], i)}/handler.py`,
      ...(r > 0.5 ? [`tests/test_${pick(["checkout", "auth"], i)}.py`] : []),
    ],
    diff: sampleDiff,
    explanation: "Live-generated patch from the agent. Inspect the diff and validation output, then approve or reject.",
    validation_output: `pytest -q\n..........\n10 passed in 1.82s\nruff: 0 errors, 0 warnings`,
    created_at: new Date().toISOString(),
  };
}

export function dashboardStats() {
  const total = errors.length;
  const fixed = errors.filter(e => e.status === "fixed").length;
  const pending = errors.filter(e => e.status === "pending" || e.status === "analyzing").length;
  const escalated = errors.filter(e => e.status === "escalated").length;
  const avgConf = Math.round(fixes.reduce((s, f) => s + f.confidence, 0) / fixes.length);
  return { total, fixed, pending, escalated, avgConf, apiHealth: 96 };
}

export function throughputSeries() {
  const now = new Date();
  return Array.from({ length: 12 }, (_, i) => {
    const t = subMinutes(now, (11 - i) * 5);
    return {
      t: t.toISOString(),
      label: `${t.getHours().toString().padStart(2, "0")}:${t.getMinutes().toString().padStart(2, "0")}`,
      ingested: Math.round(8 + rand(i + 100) * 22),
      fixed: Math.round(4 + rand(i + 200) * 16),
    };
  });
}

export function errorsByClass() {
  const map: Record<string, number> = {};
  errors.forEach(e => { map[e.error_class] = (map[e.error_class] || 0) + 1; });
  return Object.entries(map).map(([k, v]) => ({ class: k, count: v })).sort((a, b) => b.count - a.count);
}
