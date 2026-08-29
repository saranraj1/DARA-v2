import { useState, useEffect } from "react";
import { PageHeader } from "@/components/PageHeader";
import { KpiCard } from "@/components/KpiCard";
import { PanelHeader } from "@/components/PanelHeader";
import { StatusChip, statusTone } from "@/components/StatusChip";
import { dashboardStats, throughputSeries, errorsByClass, errors as errorRows } from "@/lib/mock";
import { api } from "@/lib/api";
import { rel } from "@/lib/format";
import { Area, AreaChart, Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid } from "recharts";

const chartTooltipStyle = {
  contentStyle: {
    background: "hsl(var(--surface-2))",
    border: "1px solid hsl(var(--border))",
    borderRadius: 0,
    fontFamily: "JetBrains Mono, monospace",
    fontSize: 11,
  },
  labelStyle: { color: "hsl(var(--muted-foreground))", fontSize: 10, textTransform: "uppercase" as const, letterSpacing: "0.1em" },
};

export default function Dashboard() {
  const [stats, setStats] = useState(() => dashboardStats());
  const [series, setSeries] = useState(() => throughputSeries());
  const [byClass, setByClass] = useState(() => errorsByClass());
  const [recent, setRecent] = useState(() => [...errorRows].sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at)).slice(0, 5));

  useEffect(() => {
    let active = true;
    async function loadData() {
      try {
        const [rawStats, rawThroughput, rawByClass, rawErrors] = await Promise.all([
          api.stats().catch(() => null),
          api.throughput().catch(() => null),
          api.errorsByClass().catch(() => null),
          api.errors({ page: 1, page_size: 5 }).catch(() => null),
        ]);

        if (!active) return;

        if (rawStats) {
          setStats({
            total: rawStats.total_errors ?? 0,
            fixed: rawStats.fixed ?? 0,
            pending: rawStats.pending ?? 0,
            escalated: rawStats.escalated ?? 0,
            avgConf: Math.round((rawStats.avg_confidence ?? 0) * 100),
            apiHealth: 98,
          });
        }
        if (rawThroughput && rawThroughput.buckets) {
          setSeries(rawThroughput.buckets);
        }
        if (rawByClass && rawByClass.items) {
          setByClass(rawByClass.items.map((i: any) => ({ class: i.class, count: i.count })));
        }
        if (rawErrors && rawErrors.items) {
          // map fields to match ErrorRow shape
          setRecent(rawErrors.items.map((e: any) => ({
            id: e.id,
            error_class: e.error_class,
            message: e.message,
            stack: e.stack_trace,
            service: e.service,
            repo: e.repo || "acme-corp/unknown",
            commit: e.commit_sha,
            pipeline_run_id: e.pipeline_run_id || "run-unknown",
            github_run_url: e.github_run_url || "#",
            severity: e.severity,
            status: e.status,
            created_at: e.created_at,
          })));
        }
      } catch (e) {
        console.error("Dashboard fetching error:", e);
      }
    }

    loadData();
    const timer = setInterval(loadData, 30000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <div>
      <PageHeader
        title="dashboard"
        crumbs={["dashboard"]}
        subtitle="High-level summary of the autonomous debugging pipeline. KPI counters refresh every 30 seconds."
      />

      <div className="p-6 space-y-6 animate-fade-in">
        {/* KPIs */}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <KpiCard label="// total errors" value={stats.total} hint="ingested · all-time" />
          <KpiCard label="// fixed" value={stats.fixed} hint={`${Math.round(stats.fixed / stats.total * 100)}% resolution`} tone="ok" />
          <KpiCard label="// pending" value={stats.pending} hint="awaiting hitl review" tone="warn" />
          <KpiCard label="// escalated" value={stats.escalated} hint="no fix found" tone="err" />
          <KpiCard label="// avg confidence" value={`${stats.avgConf}%`} hint="across active fixes" tone="info" />
          <KpiCard
            label="// api health"
            value={
              <div className="flex items-center gap-2">
                <span className="text-signal-ok">{stats.apiHealth}</span>
                <span className="text-base text-muted-foreground">/100</span>
              </div>
            }
            hint="all probes green"
            tone="ok"
            spark={
              <div className="flex gap-0.5">
                {[5,7,6,8,7,9,8,9].map((h, i) => (
                  <div key={i} className="w-0.5 bg-signal-ok/70" style={{ height: h * 2 }} />
                ))}
              </div>
            }
          />
        </div>

        {/* Charts row */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
          <div className="lg:col-span-2 border border-border bg-surface-1">
            <PanelHeader title="pipeline throughput" subtitle="last 60min · 5min buckets" right={<span className="label-mono">errors → fixes</span>} />
            <div className="p-3 h-[260px]">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={series} margin={{ top: 10, right: 8, bottom: 0, left: -20 }}>
                  <defs>
                    <linearGradient id="gIng" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="hsl(var(--signal-err))" stopOpacity={0.45} />
                      <stop offset="100%" stopColor="hsl(var(--signal-err))" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="gFix" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="hsl(var(--signal-ok))" stopOpacity={0.4} />
                      <stop offset="100%" stopColor="hsl(var(--signal-ok))" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="2 4" vertical={false} />
                  <XAxis dataKey="label" stroke="hsl(var(--muted-foreground))" fontSize={10} tickLine={false} axisLine={false} fontFamily="JetBrains Mono" />
                  <YAxis stroke="hsl(var(--muted-foreground))" fontSize={10} tickLine={false} axisLine={false} fontFamily="JetBrains Mono" />
                  <Tooltip {...chartTooltipStyle} cursor={{ stroke: "hsl(var(--primary))", strokeDasharray: "2 2" }} />
                  <Area type="monotone" dataKey="ingested" stroke="hsl(var(--signal-err))" strokeWidth={1.5} fill="url(#gIng)" />
                  <Area type="monotone" dataKey="fixed" stroke="hsl(var(--signal-ok))" strokeWidth={1.5} fill="url(#gFix)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="border border-border bg-surface-1">
            <PanelHeader title="errors by class" subtitle="distribution" />
            <div className="p-3 h-[260px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={byClass} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 8 }}>
                  <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="2 4" horizontal={false} />
                  <XAxis type="number" stroke="hsl(var(--muted-foreground))" fontSize={10} tickLine={false} axisLine={false} fontFamily="JetBrains Mono" />
                  <YAxis type="category" dataKey="class" stroke="hsl(var(--muted-foreground))" fontSize={10} tickLine={false} axisLine={false} width={120} fontFamily="JetBrains Mono" />
                  <Tooltip {...chartTooltipStyle} cursor={{ fill: "hsl(var(--primary) / 0.08)" }} />
                  <Bar dataKey="count" fill="hsl(var(--primary))" radius={0} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        {/* Recent outcomes */}
        <div className="border border-border bg-surface-1">
          <PanelHeader title="recent outcomes" subtitle="last 5 pipeline runs" right={<a className="label-mono hover:text-primary" href="/errors">view all →</a>} />
          <div className="divide-y divide-border">
            {recent.map((e) => (
              <div key={e.id} className="grid grid-cols-12 items-center gap-4 px-4 py-3 hover:bg-surface-2 transition-colors">
                <div className="col-span-1 label-mono">{rel(e.created_at)}</div>
                <div className="col-span-2 font-mono text-[11px] text-signal-info">{e.error_class}</div>
                <div className="col-span-5 text-xs text-foreground/90 truncate">{e.message}</div>
                <div className="col-span-2 font-mono text-[11px] text-muted-foreground">{e.service}</div>
                <div className="col-span-2 flex justify-end">
                  <StatusChip tone={statusTone(e.status)}>{e.status}</StatusChip>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
