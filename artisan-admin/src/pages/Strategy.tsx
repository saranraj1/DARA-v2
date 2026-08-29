import { useEffect, useState, useCallback } from "react";
import { PageHeader } from "@/components/PageHeader";
import { PanelHeader } from "@/components/PanelHeader";
import { StatusChip, statusTone } from "@/components/StatusChip";
import { strategiesData as seedStrategies, type StrategyRow } from "@/lib/mock";
import { format } from "date-fns";
import { api } from "@/lib/api";

export default function Strategy() {
  const [data, setData] = useState<StrategyRow[]>(seedStrategies);
  const [loading, setLoading] = useState(false);

  const loadStrategies = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.strategies();
      if (res && Array.isArray(res.items) && res.items.length > 0) {
        setData(res.items.map((item: any) => ({
          id: item.id,
          error_class: item.error_class,
          variant_name: item.variant_name,
          status: item.status,
          acceptance_rate: item.acceptance_rate,
          total_uses: item.total_uses,
          success_rate: Math.round(item.success_rate),
          generated_by: item.generated_by || "llm",
          created_at: item.created_at || new Date().toISOString(),
        })));
      } else {
        setData(seedStrategies);
      }
    } catch (err: any) {
      console.error(err);
      setData(seedStrategies);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStrategies();
  }, [loadStrategies]);

  const activeByClass = data.filter(s => s.status === "active");

  return (
    <div>
      <PageHeader
        title="strategy variants"
        crumbs={["strategy"]}
        subtitle="A/B-tested LLM prompt strategies. Each error class can have multiple variants in flight; promote the winner, retire the rest."
      />

      <div className="p-6 space-y-4 animate-fade-in">
        {/* Active banner */}
        <div className="border border-primary/40 bg-primary/5 relative overflow-hidden">
          <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-primary to-transparent" />
          <div className="px-4 py-3 flex items-center gap-3 border-b border-primary/20">
            <span className="text-primary blink">●</span>
            <span className="font-mono text-sm font-semibold text-primary">active strategies — live</span>
            <span className="label-mono ml-auto">{activeByClass.length} in production</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 divide-x divide-primary/20">
            {activeByClass.map(s => (
              <div key={s.id} className="p-4">
                <div className="label-mono">{s.error_class}</div>
                <div className="font-mono text-sm text-foreground mt-1">{s.variant_name}</div>
                <div className="mt-2 flex items-baseline gap-2">
                  <span className="font-mono text-2xl font-bold text-signal-ok tabular-nums">{s.acceptance_rate}%</span>
                  <span className="label-mono">acceptance · {s.total_uses} uses</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Table */}
        <div className="border border-border bg-surface-1">
          <PanelHeader title="all variants" subtitle="draft · testing · active · retired" />
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] font-mono">
              <thead>
                <tr className="border-b border-border bg-surface-2">
                  {["error class", "variant", "status", "acceptance", "uses", "success rate", "by", "created", "_"].map(h =>
                    <th key={h} className="px-4 py-2 text-left label-mono font-normal">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {data.map(s => (
                  <tr key={s.id} className="border-b border-border/50 hover:bg-surface-2 transition-colors">
                    <td className="px-4 py-2 text-signal-info">{s.error_class}</td>
                    <td className="px-4 py-2 text-foreground">{s.variant_name}</td>
                    <td className="px-4 py-2"><StatusChip tone={statusTone(s.status)}>{s.status}</StatusChip></td>
                    <td className="px-4 py-2 tabular-nums text-foreground">{s.acceptance_rate}%</td>
                    <td className="px-4 py-2 tabular-nums text-muted-foreground">{s.total_uses}</td>
                    <td className="px-4 py-2 w-48">
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1 bg-surface-3"><div className="h-full bg-primary" style={{ width: `${s.success_rate}%` }} /></div>
                        <span className="tabular-nums text-[11px] w-8">{s.success_rate}%</span>
                      </div>
                    </td>
                    <td className="px-4 py-2">
                      <span className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 border ${s.generated_by === "llm" ? "border-signal-violet/40 text-signal-violet" : "border-signal-info/40 text-signal-info"}`}>{s.generated_by}</span>
                    </td>
                    <td className="px-4 py-2 text-muted-foreground tabular-nums">{format(new Date(s.created_at), "yyyy-MM-dd")}</td>
                    <td className="px-4 py-2 text-right">
                      {s.status === "testing" && <button className="text-[10px] uppercase tracking-wider text-signal-ok hover:underline">promote →</button>}
                      {s.status === "active" && <button className="text-[10px] uppercase tracking-wider text-signal-err hover:underline">retire</button>}
                    </td>
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
