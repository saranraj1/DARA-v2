import { Pause, Play, Radio, Volume2, VolumeX } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  enabled: boolean;
  paused: boolean;          // user pause
  effectivePaused?: boolean; // includes external pauses
  buffered: number;
  soundOn: boolean;
  onToggleEnabled: () => void;
  onTogglePaused: () => void;
  onToggleSound: () => void;
  onFlush?: () => void;
  className?: string;
}

export function LiveTailToggle({
  enabled, paused, effectivePaused, buffered, soundOn,
  onToggleEnabled, onTogglePaused, onToggleSound,
  className,
}: Props) {
  const ePaused = effectivePaused ?? paused;
  const live = enabled && !ePaused;
  const externallyPaused = ePaused && !paused;

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <button
        onClick={onToggleSound}
        className={cn(
          "p-1.5 border transition-colors",
          soundOn
            ? "border-signal-warn/50 text-signal-warn hover:bg-signal-warn/10"
            : "border-border text-muted-foreground hover:border-foreground hover:text-foreground"
        )}
        title={soundOn ? "Critical-row beep: ON" : "Critical-row beep: OFF"}
        aria-label={soundOn ? "Disable critical sound" : "Enable critical sound"}
      >
        {soundOn ? <Volume2 className="w-3 h-3" /> : <VolumeX className="w-3 h-3" />}
      </button>

      <button
        onClick={onTogglePaused}
        disabled={!enabled}
        className={cn(
          "p-1.5 border transition-colors",
          enabled
            ? paused
              ? "border-signal-warn/50 text-signal-warn hover:bg-signal-warn/10"
              : "border-border text-muted-foreground hover:border-foreground hover:text-foreground"
            : "border-border text-muted-foreground/30 cursor-not-allowed"
        )}
        title={paused ? "Resume tail" : externallyPaused ? "Auto-paused (detail open)" : "Pause tail"}
      >
        {paused ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
      </button>

      <button
        onClick={onToggleEnabled}
        className={cn(
          "flex items-center gap-1.5 px-2 py-1 border font-mono text-[10px] uppercase tracking-wider transition-colors",
          live
            ? "border-signal-ok/50 text-signal-ok bg-signal-ok/5 hover:bg-signal-ok/10"
            : enabled && ePaused
            ? "border-signal-warn/50 text-signal-warn bg-signal-warn/5"
            : "border-border text-muted-foreground hover:border-foreground hover:text-foreground"
        )}
        title={enabled ? "Stop live tail" : "Start live tail"}
      >
        <span className="relative inline-flex w-2 h-2">
          <span
            className={cn(
              "absolute inset-0 rounded-full",
              live ? "bg-signal-ok" : enabled && ePaused ? "bg-signal-warn" : "bg-muted-foreground/40"
            )}
          />
          {live && <span className="absolute inset-0 rounded-full bg-signal-ok opacity-60 animate-ping" />}
        </span>
        <Radio className="w-3 h-3" />
        <span>
          {live
            ? "live tail"
            : externallyPaused
            ? "auto-paused"
            : enabled && paused
            ? "paused"
            : "tail off"}
        </span>
        {buffered > 0 && (
          <span className="ml-1 px-1 py-px bg-primary text-primary-foreground tabular-nums font-bold">
            +{buffered}
          </span>
        )}
      </button>
    </div>
  );
}
