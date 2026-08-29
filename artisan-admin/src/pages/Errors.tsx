import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { PanelHeader } from "@/components/PanelHeader";
import { StatusChip, severityTone, statusTone } from "@/components/StatusChip";
import { errors as seedErrors, makeLiveError, type ErrorRow } from "@/lib/mock";
import { rel, ts, short } from "@/lib/format";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { ChevronLeft, ChevronRight, ExternalLink, Search } from "lucide-react";
import { useLiveTail } from "@/hooks/useLiveTail";
import { LiveTailToggle } from "@/components/LiveTailToggle";
import { TailRateSpark } from "@/components/TailRateSpark";
import { NewRowsBanner } from "@/components/NewRowsBanner";
import { usePersistedBool } from "@/hooks/usePersistedBool";
import { beepCritical } from "@/lib/sound";

import { api } from "@/lib/api";

const STATUSES = ["all", "pending", "analyzing", "fixed", "escalated", "ignored"];
const SEVERITIES = ["all", "critical", "high", "medium", "low"];

export default function Errors() {
  const [status, setStatus] = useState("all");
  const [sev, setSev] = useState("all");
  const [svc, setSvc] = useState("");
  const [cls, setCls] = useState("");
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(20);
  const [open, setOpen] = useState<ErrorRow | null>(null);

  const [soundOn, setSoundOn] = usePersistedBool("dara.tail.errors.sound", false);
  const soundOnRef = useRef(soundOn);
  useEffect(() => { soundOnRef.current = soundOn; }, [soundOn]);

  const generate = useCallback(() => makeLiveError(), []);
  const handleArrive = useCallback((row: ErrorRow) => {
    if (soundOnRef.current && row.severity === "critical") beepCritical();
  }, []);

  const mapBackendError = useCallback((raw: any): ErrorRow => {
    return {
      id: raw.id,
      error_class: raw.error_class || "unknown_class",
      message: raw.message || "",
      stack: raw.stack_trace || raw.stack || "",
      service: raw.service || "unknown-service",
      repo: raw.repo || `acme/${raw.service || "monolith"}`,
      commit: raw.commit_sha || raw.commit || "main",
      pipeline_run_id: raw.pipeline_run_id || raw.deploy_id || "run_unknown",
      github_run_url: raw.github_run_url || `https://github.com/acme/${raw.service || "monolith"}/actions`,
      severity: raw.severity || "medium",
      status: raw.status || "pending",
      created_at: raw.created_at,
    };
  }, []);

  const fetchLatest = useCallback(async () => {
    const res = await api.errors({ page: 1, page_size: 50 });
    if (res && Array.isArray(res.items)) {
      return res.items.map(mapBackendError);
    }
    return [];
  }, [mapBackendError]);

  const tail = useLiveTail<ErrorRow>({
    storageKey: "dara.tail.errors",
    seed: seedErrors,
    generate,
    fetchLatest,
    getId: (e) => e.id,
    intervalMs: 4500,
    freshMs: 2500,
    externallyPaused: !!open, // auto-pause while detail panel open
    onArrive: handleArrive,
  });

  const filtered = useMemo(() => {
    return tail.rows.filter(e =>
      (status === "all" || e.status === status) &&
      (sev === "all" || e.severity === sev) &&
      (!svc || e.service.toLowerCase().includes(svc.toLowerCase())) &&
      (!cls || e.error_class.toLowerCase().includes(cls.toLowerCase()))
    );
  }, [tail.rows, status, sev, svc, cls]);

  const pages = Math.max(1, Math.ceil(filtered.length / size));
  const slice = filtered.slice(page * size, page * size + size);

  // "New rows since the user moved off page 1" — shown as a sticky banner.
  const sliceTopIdRef = useRef<string | null>(null);
  const [newSinceTop, setNewSinceTop] = useState(0);
  useEffect(() => {
    if (page === 0) {
      sliceTopIdRef.current = filtered[0]?.id ?? null;
      setNewSinceTop(0);
      return;
    }
    if (!sliceTopIdRef.current) {
      sliceTopIdRef.current = filtered[0]?.id ?? null;
      return;
    }
    const idx = filtered.findIndex(r => r.id === sliceTopIdRef.current);
    setNewSinceTop(idx > 0 ? idx : 0);
  }, [filtered, page]);

  const jumpToTop = () => {
    setPage(0);
    sliceTopIdRef.current = filtered[0]?.id ?? null;
    setNewSinceTop(0);
  };

  return (
    <div>
      <PageHeader
        title="errors"
        crumbs={["errors"]}
        subtitle="All errors ingested into the pipeline. Filterable, paginated, click any row for full context."
      />

      <div className="p-6 space-y-4 animate-fade-in">
        {/* Filters */}
        <div className="border border-border bg-surface-1">
          <PanelHeader title="filter" subtitle={`${filtered.length} of ${tail.rows.length} match`} />
          <div className="p-3 grid grid-cols-2 md:grid-cols-4 gap-3">
            <Select label="status" value={status} onChange={setStatus} options={STATUSES} />
            <Select label="severity" value={sev} onChange={setSev} options={SEVERITIES} />
            <TextInput label="service" placeholder="checkout-api" value={svc} onChange={setSvc} />
            <TextInput label="error class" placeholder="null_reference" value={cls} onChange={setCls} />
          </div>
        </div>

        {/* Table */}
        <div className="border border-border bg-surface-1">
          <PanelHeader
            title="error stream"
            subtitle={tail.enabled && !tail.effectivePaused ? "ingest log · streaming" : tail.effectivePaused ? (tail.paused ? "ingest log · paused" : "ingest log · auto-paused") : "ingest log"}
            right={
              <div className="flex items-center gap-4">
                <TailRateSpark data={tail.rateSeries} />
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
          <NewRowsBanner count={newSinceTop} onClick={jumpToTop} noun="errors" />
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] font-mono">
              <thead>
                <tr className="border-b border-border bg-surface-2">
                  <Th>error class</Th>
                  <Th>message</Th>
                  <Th>service</Th>
                  <Th>severity</Th>
                  <Th>status</Th>
                  <Th>created</Th>
                  <Th className="text-right pr-4">_</Th>
                </tr>
              </thead>
              <tbody>
                {slice.length === 0 && (
                  <tr><td colSpan={7} className="px-4 py-12 text-center text-muted-foreground">// no errors match the current filter</td></tr>
                )}
                {slice.map((e) => {
                  const fresh = tail.freshIds.has(e.id);
                  return (
                    <tr key={e.id} onClick={() => setOpen(e)} className={`border-b border-border/50 hover:bg-surface-2 cursor-pointer transition-colors ${fresh ? "row-fresh" : ""}`}>
                      <td className="px-4 py-2 text-signal-info">{fresh && <span className="text-primary mr-1">●</span>}{e.error_class}</td>
                      <td className="px-4 py-2 text-foreground/90 max-w-md truncate">{e.message.slice(0, 80)}</td>
                      <td className="px-4 py-2 text-muted-foreground">{e.service}</td>
                      <td className="px-4 py-2"><StatusChip tone={severityTone(e.severity)}>{e.severity}</StatusChip></td>
                      <td className="px-4 py-2"><StatusChip tone={statusTone(e.status)}>{e.status}</StatusChip></td>
                      <td className="px-4 py-2 text-muted-foreground tabular-nums">{rel(e.created_at)}</td>
                      <td className="px-4 py-2 text-right text-primary">→</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Pagination page={page} pages={pages} size={size} setPage={setPage} setSize={setSize} total={filtered.length} />
        </div>
      </div>

      <Sheet open={!!open} onOpenChange={(v) => !v && setOpen(null)}>
        <SheetContent side="right" className="w-full sm:max-w-2xl bg-background border-l border-border p-0 font-mono">
          {open && <ErrorDetail e={open} />}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function ErrorDetail({ e }: { e: ErrorRow }) {
  return (
    <div className="h-full flex flex-col">
      <div className="px-5 py-4 border-b border-border bg-surface-1">
        <div className="label-mono">// error · {short(e.id, 12)}</div>
        <div className="mt-1 text-signal-info text-sm">{e.error_class}</div>
        <div className="mt-2 text-foreground text-[13px] leading-relaxed">{e.message}</div>
        <div className="mt-3 flex flex-wrap gap-2">
          <StatusChip tone={severityTone(e.severity)}>{e.severity}</StatusChip>
          <StatusChip tone={statusTone(e.status)}>{e.status}</StatusChip>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-5 space-y-5 text-[12px]">
        <Field label="service">{e.service}</Field>
        <Field label="repo">{e.repo}</Field>
        <Field label="commit"><span className="text-primary">{e.commit}</span></Field>
        <Field label="pipeline run">{e.pipeline_run_id}</Field>
        <Field label="ingested">{ts(e.created_at)}</Field>
        <div>
          <div className="label-mono mb-1.5">stack trace</div>
          <pre className="bg-surface-1 border border-border p-3 text-[11px] leading-relaxed text-foreground/90 overflow-x-auto whitespace-pre">{e.stack}</pre>
        </div>
        <a href={e.github_run_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 text-primary hover:underline">
          <ExternalLink className="w-3.5 h-3.5" /> github actions run
        </a>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-3">
      <div className="label-mono">{label}</div>
      <div className="col-span-2 text-foreground/90 break-all">{children}</div>
    </div>
  );
}

function Th({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <th className={`px-4 py-2 text-left label-mono font-normal ${className}`}>{children}</th>;
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: string[] }) {
  return (
    <label className="block">
      <div className="label-mono mb-1">{label}</div>
      <select value={value} onChange={(ev) => onChange(ev.target.value)} className="w-full bg-surface-2 border border-border px-2.5 py-1.5 font-mono text-[12px] focus:outline-none focus:border-primary">
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
}
function TextInput({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <label className="block">
      <div className="label-mono mb-1">{label}</div>
      <div className="relative">
        <Search className="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <input value={value} onChange={(ev) => onChange(ev.target.value)} placeholder={placeholder}
               className="w-full bg-surface-2 border border-border pl-7 pr-2 py-1.5 font-mono text-[12px] focus:outline-none focus:border-primary placeholder:text-muted-foreground/50" />
      </div>
    </label>
  );
}

function Pagination({ page, pages, size, setPage, setSize, total }: any) {
  return (
    <div className="flex items-center justify-between px-4 py-2.5 border-t border-border bg-surface-2 font-mono text-[11px]">
      <div className="text-muted-foreground tabular-nums">{page * size + 1}–{Math.min((page + 1) * size, total)} of {total}</div>
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2 text-muted-foreground">
          rows
          <select value={size} onChange={(e) => { setSize(+e.target.value); setPage(0); }} className="bg-surface-1 border border-border px-1.5 py-0.5">
            {[10, 20, 50].map(n => <option key={n}>{n}</option>)}
          </select>
        </label>
        <button onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0} className="p-1 border border-border hover:border-primary disabled:opacity-30 disabled:hover:border-border"><ChevronLeft className="w-3.5 h-3.5" /></button>
        <span className="tabular-nums">{page + 1} / {pages}</span>
        <button onClick={() => setPage(Math.min(pages - 1, page + 1))} disabled={page >= pages - 1} className="p-1 border border-border hover:border-primary disabled:opacity-30 disabled:hover:border-border"><ChevronRight className="w-3.5 h-3.5" /></button>
      </div>
    </div>
  );
}
