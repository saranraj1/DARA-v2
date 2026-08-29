import { cn } from "@/lib/utils";

type Tone = "ok" | "warn" | "err" | "info" | "violet" | "muted";
const tones: Record<Tone, string> = {
  ok:     "border-signal-ok/40    text-signal-ok    bg-signal-ok/5",
  warn:   "border-signal-warn/40  text-signal-warn  bg-signal-warn/5",
  err:    "border-signal-err/50   text-signal-err   bg-signal-err/5",
  info:   "border-signal-info/40  text-signal-info  bg-signal-info/5",
  violet: "border-signal-violet/40 text-signal-violet bg-signal-violet/5",
  muted:  "border-border          text-muted-foreground bg-surface-2",
};

interface Props {
  tone?: Tone;
  children: React.ReactNode;
  dot?: boolean;
  className?: string;
}

export function StatusChip({ tone = "muted", children, dot, className }: Props) {
  return (
    <span className={cn("chip", tones[tone], className)}>
      {dot && <span className={cn("inline-block w-1.5 h-1.5 rounded-full", `bg-signal-${tone === "muted" ? "info" : tone}`)} />}
      {children}
    </span>
  );
}

export function severityTone(s: string): Tone {
  switch (s) {
    case "critical": return "err";
    case "high": return "warn";
    case "medium": return "info";
    case "low": return "muted";
    default: return "muted";
  }
}
export function statusTone(s: string): Tone {
  switch (s) {
    case "fixed":
    case "accepted":
    case "auto_merged":
    case "active":
    case "pass":
    case "approved":
      return "ok";
    case "pending":
    case "analyzing":
    case "testing":
    case "draft":
      return "warn";
    case "escalated":
    case "rejected":
    case "fail":
    case "deprecated":
    case "retired":
      return "err";
    case "ignored": return "muted";
    default: return "muted";
  }
}
