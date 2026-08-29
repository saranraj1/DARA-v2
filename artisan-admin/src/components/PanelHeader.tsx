import { cn } from "@/lib/utils";

interface Props {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
  className?: string;
}

export function PanelHeader({ title, subtitle, right, className }: Props) {
  return (
    <div className={cn("flex items-end justify-between border-b border-border px-4 py-2.5 bg-surface-1", className)}>
      <div className="flex items-baseline gap-3">
        <span className="text-primary">▸</span>
        <h2 className="font-mono text-sm font-semibold tracking-tight text-foreground">{title}</h2>
        {subtitle && <span className="label-mono">{subtitle}</span>}
      </div>
      {right}
    </div>
  );
}
