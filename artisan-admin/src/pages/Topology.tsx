import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { PageHeader } from "@/components/PageHeader";
import { PanelHeader } from "@/components/PanelHeader";
import { StatusChip } from "@/components/StatusChip";
import { topology as seedTopology, traces as seedTraces, type ServiceNode, type TraceRow } from "@/lib/mock";
import { rel, short } from "@/lib/format";
import { Minus, Plus, RotateCcw, Search } from "lucide-react";
import { api } from "@/lib/api";

export default function Topology() {
  const fgRef = useRef<any>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 480 });
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<ServiceNode | null>(null);
  const [openTrace, setOpenTrace] = useState<string | null>(null);

  const [topData, setTopData] = useState<{ nodes: any[]; edges: any[] }>(seedTopology);
  const [traceData, setTraceData] = useState<TraceRow[]>(seedTraces);

  const loadTopologyAndTraces = useCallback(async () => {
    try {
      const topRes = await api.topology();
      if (topRes && Array.isArray(topRes.edges) && topRes.edges.length > 0) {
        const edges = topRes.edges;
        const nodesMap: Record<string, { id: string; call_count: number; error_count: number; avg_latency: number }> = {};

        edges.forEach((e: any) => {
          const src = e.source_service;
          const tgt = e.target_service;
          if (!nodesMap[src]) nodesMap[src] = { id: src, call_count: 0, error_count: 0, avg_latency: 0 };
          if (!nodesMap[tgt]) nodesMap[tgt] = { id: tgt, call_count: 0, error_count: 0, avg_latency: 0 };

          nodesMap[src].call_count += e.call_count || 0;
          nodesMap[src].error_count += e.error_count || 0;
          nodesMap[src].avg_latency = Math.max(nodesMap[src].avg_latency, e.avg_latency_ms || 0);
        });

        const nodes = Object.values(nodesMap).map(n => {
          const error_rate = n.call_count > 0 ? Math.round((n.error_count / n.call_count) * 100) : 0;
          return {
            id: n.id,
            call_count: n.call_count || 10,
            error_count: n.error_count,
            error_rate,
            avg_latency: Math.round(n.avg_latency),
            last_seen: new Date().toISOString(),
          };
        });

        const mappedEdges = edges.map((e: any) => {
          const calls = e.call_count || 1;
          const error_rate = calls > 0 ? Math.round((e.error_count / calls) * 100) : 0;
          return {
            source: e.source_service,
            target: e.target_service,
            calls,
            error_rate,
          };
        });

        setTopData({ nodes, edges: mappedEdges });
      } else {
        setTopData(seedTopology);
      }
    } catch (err: any) {
      console.error("Failed to fetch topology:", err);
      setTopData(seedTopology);
    }

    try {
      const traceRes = await api.traces({ limit: 50 });
      if (traceRes && Array.isArray(traceRes.traces) && traceRes.traces.length > 0) {
        setTraceData(traceRes.traces.map((raw: any) => ({
          id: raw.trace_id,
          span_count: raw.span_count || 1,
          services: raw.services || [],
          has_error: (raw.error_span_count || 0) > 0,
          started_at: raw.started_at || new Date().toISOString(),
          duration_ms: 250,
        })));
      } else {
        setTraceData(seedTraces);
      }
    } catch (err: any) {
      console.error("Failed to fetch traces:", err);
      setTraceData(seedTraces);
    }
  }, []);

  useEffect(() => {
    loadTopologyAndTraces();
  }, [loadTopologyAndTraces]);

  const data = useMemo(() => {
    const nodes = topData.nodes
      .filter(n => !filter || n.id.toLowerCase().includes(filter.toLowerCase()))
      .map(n => ({ ...n }));
    const ids = new Set(nodes.map(n => n.id));
    const links = topData.edges
      .filter(e => ids.has(e.source) && ids.has(e.target))
      .map(e => ({ ...e }));
    return { nodes, links };
  }, [topData, filter]);

  useEffect(() => {
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ w: width, h: Math.max(420, height) });
    });
    if (wrapRef.current) ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  const incoming = (id: string) => topData.edges.filter(e => e.target === id);
  const outgoing = (id: string) => topData.edges.filter(e => e.source === id);

  return (
    <div>
      <PageHeader
        title="service topology"
        crumbs={["topology"]}
        subtitle="Live call graph from ingested OpenTelemetry traces. Nodes pulse with traffic; alerts fire above 5% error rate."
      />

      <div className="p-6 space-y-4 animate-fade-in">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
          {/* Graph */}
          <div className="lg:col-span-2 border border-border bg-surface-1">
            <PanelHeader
              title="call graph"
              subtitle={`${data.nodes.length} services · ${data.links.length} edges`}
              right={
                <div className="flex items-center gap-2">
                  <div className="relative">
                    <Search className="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="filter…"
                           className="bg-surface-2 border border-border pl-7 pr-2 py-1 font-mono text-[11px] focus:outline-none focus:border-primary placeholder:text-muted-foreground/50 w-32" />
                  </div>
                  <IconBtn onClick={() => fgRef.current?.zoom(fgRef.current.zoom() * 1.3, 300)}><Plus className="w-3.5 h-3.5" /></IconBtn>
                  <IconBtn onClick={() => fgRef.current?.zoom(fgRef.current.zoom() / 1.3, 300)}><Minus className="w-3.5 h-3.5" /></IconBtn>
                  <IconBtn onClick={() => fgRef.current?.zoomToFit(400, 40)}><RotateCcw className="w-3.5 h-3.5" /></IconBtn>
                </div>
              }
            />
            <div ref={wrapRef} className="grid-bg h-[520px] relative">
              <ForceGraph2D
                ref={fgRef}
                width={size.w}
                height={size.h}
                graphData={data as any}
                backgroundColor="rgba(0,0,0,0)"
                nodeRelSize={6}
                linkColor={() => "rgba(120,120,140,0.35)"}
                linkDirectionalArrowLength={4}
                linkDirectionalArrowRelPos={1}
                linkWidth={(l: any) => Math.min(3, Math.log10(l.calls) - 1)}
                linkLabel={(l: any) => `${l.source.id || l.source} → ${l.target.id || l.target}\ncalls: ${l.calls}\nerr: ${l.error_rate}%`}
                onNodeClick={(n: any) => setSelected(n)}
                nodeCanvasObject={(node: any, ctx, globalScale) => {
                  const alert = node.error_rate > 5;
                  const r = 8 + Math.log10(node.call_count) * 1.2;
                  // halo
                  if (alert) {
                    ctx.beginPath();
                    ctx.arc(node.x, node.y, r + 4, 0, 2 * Math.PI);
                    ctx.strokeStyle = "hsl(0 78% 60% / 0.5)";
                    ctx.lineWidth = 1;
                    ctx.stroke();
                  }
                  ctx.beginPath();
                  ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
                  ctx.fillStyle = alert ? "hsl(0 78% 60%)" : selected?.id === node.id ? "hsl(38 92% 58%)" : "hsl(199 89% 60%)";
                  ctx.fill();
                  ctx.strokeStyle = "hsl(220 14% 7%)";
                  ctx.lineWidth = 1.5;
                  ctx.stroke();

                  const label = node.id;
                  const fontSize = 11 / globalScale;
                  ctx.font = `${fontSize}px JetBrains Mono`;
                  ctx.textAlign = "center";
                  ctx.textBaseline = "top";
                  ctx.fillStyle = "hsl(60 12% 88%)";
                  ctx.fillText(label, node.x, node.y + r + 2);
                }}
              />
              {/* legend */}
              <div className="absolute bottom-2 left-2 flex gap-3 text-[10px] font-mono text-muted-foreground bg-surface-1/80 backdrop-blur px-2 py-1 border border-border">
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-signal-info" /> healthy</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-signal-err" /> err &gt; 5%</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-primary" /> selected</span>
              </div>
            </div>
          </div>

          {/* Service detail */}
          <div className="border border-border bg-surface-1">
            <PanelHeader title="service detail" subtitle={selected ? selected.id : "click a node"} />
            {!selected ? (
              <div className="p-8 text-center text-muted-foreground text-xs">// select a service node to inspect incoming/outgoing edges, latency, and error rate.</div>
            ) : (
              <div className="p-4 space-y-4 text-[12px] font-mono">
                <div>
                  <div className="font-bold text-foreground text-sm">{selected.id}</div>
                  <div className="label-mono mt-0.5">last seen {rel(selected.last_seen)}</div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <Stat label="calls" value={selected.call_count.toLocaleString()} />
                  <Stat label="errors" value={selected.error_count} tone={selected.error_count > 50 ? "err" : undefined} />
                  <Stat label="err rate" value={`${selected.error_rate}%`} tone={selected.error_rate > 5 ? "err" : "ok"} />
                  <Stat label="avg latency" value={`${selected.avg_latency}ms`} />
                </div>
                <div>
                  <div className="label-mono mb-1.5">↓ incoming</div>
                  {incoming(selected.id).length ? incoming(selected.id).map(e => (
                    <div key={e.source} className="flex justify-between py-0.5 text-foreground/90"><span>{e.source}</span><span className="text-muted-foreground tabular-nums">{e.calls}</span></div>
                  )) : <div className="text-muted-foreground/60">none</div>}
                </div>
                <div>
                  <div className="label-mono mb-1.5">↑ outgoing</div>
                  {outgoing(selected.id).length ? outgoing(selected.id).map(e => (
                    <div key={e.target} className="flex justify-between py-0.5 text-foreground/90"><span>{e.target}</span><span className="text-muted-foreground tabular-nums">{e.calls}</span></div>
                  )) : <div className="text-muted-foreground/60">none</div>}
                </div>
                <a href={`/errors?service=${selected.id}`} className="block text-center py-2 border border-border hover:border-primary hover:text-primary text-[11px] uppercase tracking-wider transition-colors">
                  view errors for this service →
                </a>
              </div>
            )}
          </div>
        </div>

        {/* Recent traces */}
        <div className="border border-border bg-surface-1">
          <PanelHeader title="recent traces" subtitle={`${traceData.length} captured`} />
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] font-mono">
              <thead>
                <tr className="border-b border-border bg-surface-2">
                  {["trace id", "spans", "services", "error", "started", "duration", ""].map(h =>
                    <th key={h} className="px-4 py-2 text-left label-mono font-normal">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {traceData.map(t => {
                  const open = openTrace === t.id;
                  return (
                    <tr key={t.id} className="border-b border-border/50 hover:bg-surface-2 transition-colors cursor-pointer align-top" onClick={() => setOpenTrace(open ? null : t.id)}>
                      <td className="px-4 py-2 text-primary">{short(t.id, 12)}</td>
                      <td className="px-4 py-2 tabular-nums text-foreground">{t.span_count}</td>
                      <td className="px-4 py-2 text-muted-foreground text-[11px] max-w-md">
                        <div className="truncate">{t.services.join(" → ")}</div>
                        {open && (
                          <div className="mt-3 space-y-1 not-italic">
                            {t.services.map((s, i) => {
                              const w = 100 - i * (60 / t.services.length);
                              const off = i * (10 / t.services.length);
                              return (
                                <div key={i} className="flex items-center gap-3 text-[11px]">
                                  <span className="w-32 text-muted-foreground truncate">{s}</span>
                                  <div className="flex-1 h-3 bg-surface-3 relative min-w-[120px]">
                                    <div className="absolute top-0 h-full bg-signal-info/60" style={{ left: `${off}%`, width: `${w}%` }} />
                                  </div>
                                  <span className="tabular-nums text-foreground w-12 text-right">{Math.round(t.duration_ms * w / 100)}ms</span>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-2">{t.has_error ? <StatusChip tone="err">yes</StatusChip> : <StatusChip tone="ok">no</StatusChip>}</td>
                      <td className="px-4 py-2 text-muted-foreground tabular-nums">{rel(t.started_at)}</td>
                      <td className="px-4 py-2 tabular-nums text-foreground">{t.duration_ms}ms</td>
                      <td className="px-4 py-2 text-primary text-right pr-4">{open ? "▾" : "▸"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "ok" | "err" }) {
  const c = tone === "err" ? "text-signal-err" : tone === "ok" ? "text-signal-ok" : "text-foreground";
  return (
    <div className="border border-border bg-background p-2">
      <div className="label-mono">{label}</div>
      <div className={`mt-0.5 font-mono text-base tabular-nums ${c}`}>{value}</div>
    </div>
  );
}
function IconBtn({ children, onClick }: any) {
  return <button onClick={onClick} className="p-1.5 border border-border bg-surface-2 hover:border-primary hover:text-primary transition-colors">{children}</button>;
}
