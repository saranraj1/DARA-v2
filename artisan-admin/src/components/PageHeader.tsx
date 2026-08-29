import { useEffect, useState } from "react";
import { RotateCw } from "lucide-react";
import { tsShort } from "@/lib/format";

interface Props {
  title: string;
  subtitle?: string;
  crumbs?: string[];
}

export function PageHeader({ title, subtitle, crumbs = [] }: Props) {
  const [updated, setUpdated] = useState(new Date());
  const [spinning, setSpinning] = useState(false);

  useEffect(() => {
    const t = setInterval(() => setUpdated(new Date()), 30000);
    return () => clearInterval(t);
  }, []);

  const refresh = () => {
    setSpinning(true);
    setTimeout(() => { setUpdated(new Date()); setSpinning(false); }, 450);
  };

  return (
    <div className="border-b border-border bg-surface-1">
      <div className="flex items-center justify-between px-6 py-4">
        <div>
          <div className="label-mono mb-1.5 flex items-center gap-2">
            <span className="text-primary">$</span>
            <span>dara</span>
            {crumbs.map((c, i) => (
              <span key={i} className="flex items-center gap-2">
                <span className="text-border">/</span>
                <span>{c}</span>
              </span>
            ))}
          </div>
          <h1 className="font-mono text-2xl font-bold tracking-tight text-foreground">
            {title}
            <span className="text-primary blink ml-1">_</span>
          </h1>
          {subtitle && <p className="text-xs text-muted-foreground mt-1 max-w-2xl">{subtitle}</p>}
        </div>

        <div className="flex items-center gap-3">
          <div className="text-right">
            <div className="label-mono">last sync</div>
            <div className="text-[11px] font-mono text-foreground tabular-nums">{tsShort(updated.toISOString())}Z</div>
          </div>
          <button
            onClick={refresh}
            className="group flex items-center gap-2 px-3 py-2 border border-border bg-surface-2 hover:border-primary hover:text-primary transition-colors font-mono text-[11px] uppercase tracking-wider"
          >
            <RotateCw className={`w-3.5 h-3.5 ${spinning ? "animate-spin" : ""}`} />
            sync
          </button>
        </div>
      </div>

      {/* live status strip */}
      <div className="flex items-center gap-6 px-6 py-1.5 border-t border-border bg-background text-[10px] font-mono text-muted-foreground tabular-nums">
        <span className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 bg-signal-ok rounded-full" />
          api: <span className="text-signal-ok">connected</span>
        </span>
        <span>auth: <span className="text-foreground">x-admin-token</span></span>
        <span>polling: <span className="text-foreground">30s</span></span>
        <span>region: <span className="text-foreground">us-east-1</span></span>
        <span className="ml-auto text-muted-foreground/70">[?] help · [/] search · [g] navigate</span>
      </div>
    </div>
  );
}
