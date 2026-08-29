import { useEffect, useState, useMemo, useCallback } from "react";
import { PageHeader } from "@/components/PageHeader";
import { PanelHeader } from "@/components/PanelHeader";
import { StatusChip, statusTone } from "@/components/StatusChip";
import { audit as seedAudit, type AuditRow } from "@/lib/mock";
import { ts, short } from "@/lib/format";
import { Download } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";

export default function Audit() {
  const [data, setData] = useState<AuditRow[]>(seedAudit);
  const [action, setAction] = useState("all");
  const [reviewer, setReviewer] = useState("");
  const [loading, setLoading] = useState(false);

  const loadAuditLogs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.audit({ limit: 100 });
      if (res && Array.isArray(res.items) && res.items.length > 0) {
        setData(res.items.map((raw: any) => {
          let actionVal: AuditRow["action"] = "approved";
          if (raw.action === "fix_rejected" || raw.action === "rejected" || String(raw.action).includes("reject")) {
            actionVal = "rejected";
          }
          return {
            id: raw.id,
            timestamp: raw.created_at || raw.timestamp,
            action: actionVal,
            fix_id: raw.fix_id || "unknown_fix",
            error_class: raw.error_class || "unknown_class",
            reviewer: raw.reviewer || "system",
            comment: raw.comment || undefined,
            confidence: raw.confidence ? Math.round(raw.confidence <= 1.0 ? raw.confidence * 100 : raw.confidence) : 80,
          };
        }));
      } else {
        setData(seedAudit);
      }
    } catch (err: any) {
      console.error("Failed to load audit logs:", err);
      setData(seedAudit);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAuditLogs();
  }, [loadAuditLogs]);

  const rows = useMemo(() => data.filter(a =>
    (action === "all" || a.action === action) &&
    (!reviewer || a.reviewer.toLowerCase().includes(reviewer.toLowerCase()))
  ), [data, action, reviewer]);

  const exportCsv = () => {
    const header = ["timestamp", "action", "fix_id", "error_class", "reviewer", "comment", "confidence"];
    const csv = [header.join(","), ...rows.map(r => [
      r.timestamp, r.action, r.fix_id, r.error_class, r.reviewer, `"${r.comment ?? ""}"`, r.confidence,
    ].join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = "dara-audit.csv"; a.click();
    URL.revokeObjectURL(url);
    toast.success(`exported ${rows.length} rows`);
  };

  return (
    <div>
      <PageHeader
        title="audit log"
        crumbs={["audit"]}
        subtitle="Immutable record of every HITL decision. Each row corresponds to a POST /api/v1/fixes/{id}/{approve|reject} call."
      />

      <div className="p-6 space-y-4 animate-fade-in">
        <div className="border border-border bg-surface-1">
          <PanelHeader title="filter & export" right={
            <button onClick={exportCsv} className="flex items-center gap-2 px-3 py-1.5 border border-border hover:border-primary hover:text-primary font-mono text-[11px] uppercase tracking-wider transition-colors">
              <Download className="w-3.5 h-3.5" /> export csv
            </button>
          } />
          <div className="p-3 grid grid-cols-1 md:grid-cols-3 gap-3">
            <label>
              <div className="label-mono mb-1">action</div>
              <select value={action} onChange={e => setAction(e.target.value)} className="w-full bg-surface-2 border border-border px-2.5 py-1.5 font-mono text-[12px] focus:outline-none focus:border-primary">
                {["all", "approved", "rejected"].map(o => <option key={o}>{o}</option>)}
              </select>
            </label>
            <label>
              <div className="label-mono mb-1">reviewer</div>
              <input value={reviewer} onChange={e => setReviewer(e.target.value)} placeholder="alice@acme"
                     className="w-full bg-surface-2 border border-border px-2.5 py-1.5 font-mono text-[12px] focus:outline-none focus:border-primary placeholder:text-muted-foreground/50" />
            </label>
            <div className="flex items-end label-mono text-muted-foreground">{rows.length} entries</div>
          </div>
        </div>

        <div className="border border-border bg-surface-1">
          <PanelHeader title="decisions" subtitle="immutable · append-only" />
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] font-mono">
              <thead>
                <tr className="border-b border-border bg-surface-2">
                  {["timestamp", "action", "fix id", "error class", "reviewer", "comment", "confidence"].map(h =>
                    <th key={h} className="px-4 py-2 text-left label-mono font-normal">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.id} className="border-b border-border/50 hover:bg-surface-2 transition-colors">
                    <td className="px-4 py-2 tabular-nums text-muted-foreground">{ts(r.timestamp)}</td>
                    <td className="px-4 py-2"><StatusChip tone={statusTone(r.action)}>{r.action}</StatusChip></td>
                    <td className="px-4 py-2 text-primary">{short(r.fix_id, 10)}</td>
                    <td className="px-4 py-2 text-signal-info">{r.error_class}</td>
                    <td className="px-4 py-2 text-foreground">{r.reviewer}</td>
                    <td className="px-4 py-2 text-muted-foreground italic max-w-md truncate">{r.comment || <span className="not-italic text-muted-foreground/40">—</span>}</td>
                    <td className="px-4 py-2 tabular-nums text-foreground">{r.confidence}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
