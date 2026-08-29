import { useEffect, useState, useCallback } from "react";
import { PageHeader } from "@/components/PageHeader";
import { PanelHeader } from "@/components/PanelHeader";
import { StatusChip, statusTone } from "@/components/StatusChip";
import { patterns as seedPatterns, type PatternRow } from "@/lib/mock";
import { rel } from "@/lib/format";
import { Pencil } from "lucide-react";
import { api } from "@/lib/api";
import { toast } from "sonner";

export default function Patterns() {
  const [data, setData] = useState<PatternRow[]>(seedPatterns);
  const [loading, setLoading] = useState(false);

  const loadPatterns = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.patterns();
      if (res && Array.isArray(res.items) && res.items.length > 0) {
        setData(res.items.map((raw: any) => ({
          id: raw.id,
          name: `${raw.error_class} Matcher (${raw.language || "Python"})`,
          error_class: raw.error_class,
          description: `Auto-generated template matching similar ${raw.error_class} errors.`,
          match_count: raw.times_used || 0,
          success_rate: raw.success_rate
            ? (raw.success_rate <= 1.0 ? Math.round(raw.success_rate * 100) : Math.round(raw.success_rate))
            : 100,
          threshold: 75,
          last_matched: raw.last_used_at || new Date().toISOString(),
          status: "active",
        })));
      } else {
        setData(seedPatterns);
      }
    } catch (err: any) {
      console.error(err);
      // Fallback is silent or subtle warning
      setData(seedPatterns);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPatterns();
  }, [loadPatterns]);

  return (
    <div>
      <PageHeader
        title="pattern library"
        crumbs={["patterns"]}
        subtitle="Recurring error signatures DARA has learned from accepted fixes. Each pattern represents a solved class of problems."
      />

      <div className="p-6 animate-fade-in">
        {data.length === 0 ? (
          <div className="border border-dashed border-border bg-surface-1 p-12 text-center">
            <div className="label-mono mb-2">// empty state</div>
            <p className="text-muted-foreground text-sm">Patterns are auto-created after fixes are accepted. Approve a fix in the HITL queue to seed the library.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {data.map(p => (
              <article key={p.id} className="relative bg-surface-1 border border-border p-4 hover:border-primary/40 transition-colors group">
                <div className="absolute top-0 left-0 w-2 h-2 border-t border-l border-primary/60" />
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div>
                    <div className="label-mono mb-1">{p.error_class}</div>
                    <h3 className="font-mono text-sm font-semibold text-foreground leading-snug">{p.name}</h3>
                  </div>
                  <StatusChip tone={statusTone(p.status)}>{p.status}</StatusChip>
                </div>
                <p className="text-[12px] text-muted-foreground leading-relaxed mb-4">{p.description}</p>

                <dl className="grid grid-cols-2 gap-x-4 gap-y-2 font-mono text-[11px] border-t border-border pt-3">
                  <div>
                    <dt className="label-mono">matches</dt>
                    <dd className="tabular-nums text-foreground text-base">{p.match_count}</dd>
                  </div>
                  <div>
                    <dt className="label-mono">success rate</dt>
                    <dd className="flex items-center gap-2 mt-0.5">
                      <div className="flex-1 h-1 bg-surface-3"><div className="h-full bg-signal-ok" style={{ width: `${p.success_rate}%` }} /></div>
                      <span className="tabular-nums text-foreground">{p.success_rate}%</span>
                    </dd>
                  </div>
                  <div>
                    <dt className="label-mono">threshold</dt>
                    <dd className="tabular-nums text-foreground">≥ {p.threshold}</dd>
                  </div>
                  <div>
                    <dt className="label-mono">last match</dt>
                    <dd className="text-foreground">{rel(p.last_matched)}</dd>
                  </div>
                </dl>

                <button className="absolute bottom-3 right-3 opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1 text-[10px] uppercase tracking-wider text-primary hover:underline">
                  <Pencil className="w-3 h-3" /> edit
                </button>
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
