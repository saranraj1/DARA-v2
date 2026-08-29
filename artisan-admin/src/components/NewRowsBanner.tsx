import { ArrowUp } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  count: number;
  onClick: () => void;
  className?: string;
  noun?: string;
}

export function NewRowsBanner({ count, onClick, className, noun = "rows" }: Props) {
  if (count <= 0) return null;
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full flex items-center justify-center gap-2 px-3 py-1.5 border-x border-b border-primary/40 bg-primary/10 hover:bg-primary/20 transition-colors font-mono text-[11px] uppercase tracking-wider text-primary group",
        className
      )}
    >
      <ArrowUp className="w-3.5 h-3.5 group-hover:-translate-y-0.5 transition-transform" />
      <span className="tabular-nums font-bold">{count}</span>
      <span className="text-primary/80">new {noun}</span>
      <span className="text-primary/40">·</span>
      <span className="text-primary/80">jump to top</span>
    </button>
  );
}
