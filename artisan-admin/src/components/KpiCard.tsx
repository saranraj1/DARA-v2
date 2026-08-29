import { cn } from "@/lib/utils";

interface Props {
  label: string;
  value: React.ReactNode;
  hint?: string;
  spark?: React.ReactNode;
  tone?: "default" | "ok" | "warn" | "err" | "info";
  className?: string;
}

const toneRing: Record<NonNullable<Props["tone"]>, string> = {
  default: "border-border",
  ok: "border-signal-ok/30",
  warn: "border-signal-warn/30",
  err: "border-signal-err/30",
  info: "border-signal-info/30",
};

export function KpiCard({ label, value, hint, spark, tone = "default", className }: Props) {
  return (
    <div className={cn("relative bg-surface-1 border", toneRing[tone], "p-4 group hover:bg-surface-2 transition-colors", className)}>
      <div className="flex items-start justify-between">
        <div className="label-mono">{label}</div>
        {spark}
      </div>
      <div className="mt-3 font-mono text-3xl font-semibold tabular-nums tracking-tight text-foreground">
        {value}
      </div>
      {hint && <div className="mt-1 text-[11px] text-muted-foreground font-mono">{hint}</div>}
      <div className="absolute top-0 left-0 w-2 h-2 border-t border-l border-primary/60" />
      <div className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-primary/60 opacity-0 group-hover:opacity-100 transition-opacity" />
    </div>
  );
}
