import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { PanelHeader } from "@/components/PanelHeader";
import { StatusChip, statusTone } from "@/components/StatusChip";
import { fixes as seedFixes, makeLiveFix, type FixRow } from "@/lib/mock";
import { rel, ts, short } from "@/lib/format";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Check, ExternalLink, X } from "lucide-react";
import { toast } from "sonner";
import { useLiveTail } from "@/hooks/useLiveTail";
import { LiveTailToggle } from "@/components/LiveTailToggle";
import { TailRateSpark } from "@/components/TailRateSpark";
import { usePersistedBool } from "@/hooks/usePersistedBool";
import { beepCritical } from "@/lib/sound";

import { api } from "@/lib/api";

const OUTCOMES = ["all", "pending", "accepted", "rejected", "auto_merged"];

export default function Fixes() {
  const [outcome, setOutcome] = useState("all");
  const [open, setOpen] = useState<FixRow | null>(null);

  const [soundOn, setSoundOn] = usePersistedBool("dara.tail.fixes.sound", false);
  const soundOnRef = useRef(soundOn);
  useEffect(() => { soundOnRef.current = soundOn; }, [soundOn]);

  const generate = useCallback(() => makeLiveFix(), []);
  const handleArrive = useCallback((row: FixRow) => {
    // For fixes the "critical" event is a failed sandbox validation.
    if (soundOnRef.current && row.validation === "fail") beepCritical();
  }, []);

  const mapBackendFix = useCallback((raw: any): FixRow => {
    let validation: FixRow["validation"] = "pending";
    if (raw.validation_pass === true) validation = "pass";
    if (raw.validation_pass === false) validation = "fail";

    return {
      id: raw.id,
      error_id: raw.error_id,
      error_class: raw.error_class || "unknown_class",
      strategy: raw.strategy || "llm_single_file",
      confidence: raw.confidence ? Math.round(raw.confidence * 100) : 50,
      validation,
      outcome: raw.outcome || "pending",
      pr_url: raw.pr_url || undefined,
      files_changed: raw.files_changed || [],
      diff: raw.patch_preview || raw.diff || "",
      explanation: raw.reviewer_notes || "Generated fix awaiting review.",
      validation_output: raw.test_results ? JSON.stringify(raw.test_results, null, 2) : "No validation logs available.",
      created_at: raw.created_at,
    };
  }, []);

  const fetchLatest = useCallback(async () => {
    const res = await api.fixes({ page: 1, page_size: 50 });
    if (res && Array.isArray(res.items)) {
      return res.items.map(mapBackendFix);
    }
    return [];
  }, [mapBackendFix]);

  const tail = useLiveTail<FixRow>({
    storageKey: "dara.tail.fixes",
    seed: seedFixes,
    generate,
    fetchLatest,
    getId: (f) => f.id,
    intervalMs: 6000,
    freshMs: 2500,
    externallyPaused: !!open,
    onArrive: handleArrive,
  });

  // Local override map so HITL decisions stick across stream updates.
  const [overrides, setOverrides] = useState<Record<string, FixRow["outcome"]>>({});
  const data = useMemo(
    () => tail.rows.map(f => overrides[f.id] ? { ...f, outcome: overrides[f.id] } : f),
    [tail.rows, overrides],
  );
  const list = useMemo(() => outcome === "all" ? data : data.filter(f => f.outcome === outcome), [outcome, data]);

  const decide = async (id: string, action: "accepted" | "rejected", comment = "") => {
    setOverrides(prev => ({ ...prev, [id]: action }));
    if (open?.id === id) setOpen(prev => prev ? { ...prev, outcome: action } : null);

    try {
      if (id.startsWith("fix_")) {
        toast.info(`Mock fix ${short(id)} updated locally (mock fallback)`);
        return;
      }

      if (action === "accepted") {
        await api.approveFix(id, comment);
      } else {
        await api.rejectFix(id, comment);
      }
      toast.success(`fix ${short(id)} → ${action}`);
    } catch (err: any) {
      console.error(err);
      toast.error(`Failed to submit decision: ${err.message || String(err)}`);
    }
  };

  return (
    <div>
      <PageHeader
        title="fixes · hitl queue"
        crumbs={["fixes"]}
        subtitle="Human-in-the-loop review of AI-generated patches. Approve to merge, reject with rationale, or let auto-merge handle high-confidence fixes."
      />

      <div className="p-6 space-y-4 animate-fade-in">
        <div className="border border-border bg-surface-1">
          <PanelHeader title="filter" subtitle={`${list.length} fixes`} />
          <div className="p-3 flex gap-2 flex-wrap">
            {OUTCOMES.map(o => (
              <button key={o} onClick={() => setOutcome(o)}
                className={`px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider border transition-colors ${outcome === o ? "border-primary text-primary bg-primary/5" : "border-border text-muted-foreground hover:border-foreground hover:text-foreground"}`}>
                {o}
              </button>
            ))}
          </div>
        </div>

        <div className="border border-border bg-surface-1">
          <PanelHeader
            title="patch queue"
            subtitle={tail.enabled && !tail.effectivePaused ? "awaiting review · streaming" : tail.effectivePaused ? (tail.paused ? "awaiting review · paused" : "awaiting review · auto-paused") : "awaiting review"}
            right={
              <div className="flex items-center gap-4">
                <TailRateSpark data={tail.rateSeries} label="fixes/min" />
                <LiveTailToggle
                  enabled={tail.enabled}
                  paused={tail.paused}
                  effectivePaused={tail.effectivePaused}
                  buffered={tail.buffered}
                  soundOn={soundOn}
                  onToggleSound={() => setSoundOn(v => !v)}
                  onToggleEnabled={() => tail.setEnabled(!tail.enabled)}
                  onTogglePaused={() => tail.setPaused(!tail.paused)}
                />
              </div>
            }
          />
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] font-mono">
              <thead>
                <tr className="border-b border-border bg-surface-2">
                  <Th>fix id</Th>
                  <Th>error</Th>
                  <Th>strategy</Th>
                  <Th className="w-40">confidence</Th>
                  <Th>validation</Th>
                  <Th>outcome</Th>
                  <Th>pr</Th>
                  <Th>files</Th>
                  <Th className="text-right pr-4">action</Th>
                </tr>
              </thead>
              <tbody>
                {list.map(f => {
                  const fresh = tail.freshIds.has(f.id);
                  return (
                    <tr key={f.id} className={`border-b border-border/50 hover:bg-surface-2 transition-colors ${fresh ? "row-fresh" : ""}`}>
                      <td className="px-4 py-2"><button onClick={() => setOpen(f)} className="text-primary hover:underline">{fresh && <span className="mr-1">●</span>}{short(f.id, 10)}</button></td>
                      <td className="px-4 py-2 text-signal-info">{f.error_class}</td>
                      <td className="px-4 py-2 text-muted-foreground">{f.strategy}</td>
                      <td className="px-4 py-2"><ConfBar value={f.confidence} /></td>
                      <td className="px-4 py-2"><StatusChip tone={statusTone(f.validation)}>{f.validation}</StatusChip></td>
                      <td className="px-4 py-2"><StatusChip tone={statusTone(f.outcome)}>{f.outcome.replace("_", " ")}</StatusChip></td>
                      <td className="px-4 py-2">
                        {f.pr_url ? <a href={f.pr_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">#{f.pr_url.split("/").pop()} <ExternalLink className="w-3 h-3" /></a> : <span className="text-muted-foreground/50">—</span>}
                      </td>
                      <td className="px-4 py-2 text-muted-foreground text-[11px] max-w-[200px] truncate">{f.files_changed.join(", ")}</td>
                      <td className="px-4 py-2 text-right">
                        {f.outcome === "pending" ? (
                          <div className="inline-flex gap-1">
                            <button onClick={() => decide(f.id, "accepted")} className="px-2 py-1 border border-signal-ok/40 text-signal-ok hover:bg-signal-ok/10 text-[10px] uppercase tracking-wider"><Check className="w-3 h-3 inline" /> approve</button>
                            <button onClick={() => decide(f.id, "rejected")} className="px-2 py-1 border border-signal-err/40 text-signal-err hover:bg-signal-err/10 text-[10px] uppercase tracking-wider"><X className="w-3 h-3 inline" /> reject</button>
                          </div>
                        ) : <span className="text-muted-foreground/50 text-[10px]">{rel(f.created_at)}</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <Sheet open={!!open} onOpenChange={(v) => !v && setOpen(null)}>
        <SheetContent side="right" className="w-full sm:max-w-3xl bg-background border-l border-border p-0 font-mono">
          {open && <FixDetail f={open} onDecide={decide} />}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function ConfBar({ value }: { value: number }) {
  const tone = value >= 80 ? "bg-signal-ok" : value >= 60 ? "bg-signal-warn" : "bg-signal-err";
  return (
    <div className="flex items-center gap-2">
      <div className="w-24 h-1.5 bg-surface-3 relative">
        <div className={`absolute left-0 top-0 h-full ${tone}`} style={{ width: `${value}%` }} />
      </div>
      <span className="text-[11px] tabular-nums text-foreground w-8">{value}%</span>
    </div>
  );
}

function FixDetail({ f, onDecide }: { f: FixRow; onDecide: (id: string, a: "accepted" | "rejected", comment?: string) => void }) {
  const [comment, setComment] = useState("");
  return (
    <div className="h-full flex flex-col">
      <div className="px-5 py-4 border-b border-border bg-surface-1">
        <div className="label-mono">// fix · {short(f.id, 14)}</div>
        <div className="mt-1 flex items-center gap-3">
          <span className="text-signal-info text-sm">{f.error_class}</span>
          <span className="text-muted-foreground text-xs">via {f.strategy}</span>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <StatusChip tone={statusTone(f.validation)}>validation: {f.validation}</StatusChip>
          <StatusChip tone={statusTone(f.outcome)}>{f.outcome.replace("_", " ")}</StatusChip>
          <span className="ml-auto text-[11px] text-muted-foreground">confidence <span className="text-foreground tabular-nums">{f.confidence}%</span></span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <Section title="root cause">
          <p className="text-[12px] leading-relaxed text-foreground/90">{f.explanation}</p>
        </Section>

        <Section title={`diff · ${f.files_changed.length} file${f.files_changed.length > 1 ? "s" : ""}`}>
          <pre className="bg-surface-1 border border-border text-[11px] leading-relaxed overflow-x-auto">
            {f.diff.split("\n").map((line, i) => {
              const isAdd = line.startsWith("+") && !line.startsWith("+++");
              const isDel = line.startsWith("-") && !line.startsWith("---");
              const isMeta = line.startsWith("@@") || line.startsWith("diff") || line.startsWith("---") || line.startsWith("+++");
              return (
                <div key={i} className={`px-3 ${isAdd ? "bg-signal-ok/10 text-signal-ok" : isDel ? "bg-signal-err/10 text-signal-err" : isMeta ? "text-signal-violet" : "text-foreground/80"}`}>
                  <span className="select-none text-muted-foreground/40 inline-block w-6 text-right pr-2">{i + 1}</span>
                  {line || " "}
                </div>
              );
            })}
          </pre>
        </Section>

        <Section title="validation output">
          <pre className="bg-surface-1 border border-border p-3 text-[11px] leading-relaxed text-foreground/90 overflow-x-auto whitespace-pre">{f.validation_output}</pre>
        </Section>
      </div>

      {f.outcome === "pending" && (
        <div className="border-t border-border bg-surface-1 p-4 space-y-3">
          <textarea value={comment} onChange={e => setComment(e.target.value)} placeholder="// optional reviewer comment"
                    className="w-full bg-background border border-border p-2 text-[12px] focus:outline-none focus:border-primary resize-none" rows={2} />
          <div className="flex gap-2">
            <button onClick={() => onDecide(f.id, "accepted", comment)} className="flex-1 py-2 border border-signal-ok/50 text-signal-ok hover:bg-signal-ok/10 text-[11px] uppercase tracking-wider font-medium">
              <Check className="w-3.5 h-3.5 inline mr-1" /> approve & merge
            </button>
            <button onClick={() => onDecide(f.id, "rejected", comment)} className="flex-1 py-2 border border-signal-err/50 text-signal-err hover:bg-signal-err/10 text-[11px] uppercase tracking-wider font-medium">
              <X className="w-3.5 h-3.5 inline mr-1" /> reject
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-b border-border">
      <div className="px-5 py-2 label-mono bg-surface-2/50">{title}</div>
      <div className="p-5">{children}</div>
    </div>
  );
}

function Th({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <th className={`px-4 py-2 text-left label-mono font-normal ${className}`}>{children}</th>;
}
