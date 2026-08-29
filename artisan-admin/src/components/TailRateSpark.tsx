import { cn } from "@/lib/utils";

interface Props {
  data: number[];     // oldest → newest, one value per minute
  className?: string;
  label?: string;
}

/**
 * Tiny inline arrival-rate sparkline. Renders as bars to keep the
 * "engineering instrumentation" feel of the rest of the console.
 */
export function TailRateSpark({ data, className, label = "rows/min" }: Props) {
  const max = Math.max(1, ...data);
  const total = data.reduce((a, b) => a + b, 0);
  const recent = data[data.length - 1] ?? 0;

  return (
    <div className={cn("flex items-center gap-2 font-mono text-[10px]", className)}>
      <div className="label-mono">{label}</div>
      <div className="flex items-end gap-px h-5" aria-hidden="true">
        {data.map((v, i) => {
          const h = Math.max(1, Math.round((v / max) * 18));
          const isLatest = i === data.length - 1;
          return (
            <div
              key={i}
              className={cn(
                "w-1.5",
                isLatest ? "bg-signal-ok" : v > 0 ? "bg-primary/70" : "bg-border"
              )}
              style={{ height: `${h}px` }}
              title={`${v} in ${data.length - 1 - i === 0 ? "this minute" : `${data.length - 1 - i}m ago`}`}
            />
          );
        })}
      </div>
      <div className="text-foreground tabular-nums">
        <span className="text-signal-ok">{recent}</span>
        <span className="text-muted-foreground/60">/{total}</span>
      </div>
    </div>
  );
}
