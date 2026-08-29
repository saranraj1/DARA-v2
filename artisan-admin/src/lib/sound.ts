// Tiny WebAudio "terminal beep" — short square pulse with a fast envelope.
// One shared AudioContext, lazy-initialised on first user gesture / first beep.

let ctx: AudioContext | null = null;

function getCtx(): AudioContext | null {
  if (typeof window === "undefined") return null;
  if (!ctx) {
    const Ctor = (window.AudioContext || (window as any).webkitAudioContext) as typeof AudioContext | undefined;
    if (!Ctor) return null;
    ctx = new Ctor();
  }
  // Some browsers suspend the context until a gesture; resume best-effort.
  if (ctx.state === "suspended") void ctx.resume().catch(() => {});
  return ctx;
}

interface BeepOpts {
  freq?: number;       // Hz
  durationMs?: number;
  volume?: number;     // 0..1
  type?: OscillatorType;
}

export function beep({ freq = 880, durationMs = 90, volume = 0.06, type = "square" }: BeepOpts = {}) {
  const ac = getCtx();
  if (!ac) return;
  const t0 = ac.currentTime;
  const t1 = t0 + durationMs / 1000;

  const osc = ac.createOscillator();
  const gain = ac.createGain();

  osc.type = type;
  osc.frequency.setValueAtTime(freq, t0);

  // Fast attack, exponential decay — short and unobtrusive.
  gain.gain.setValueAtTime(0.0001, t0);
  gain.gain.exponentialRampToValueAtTime(volume, t0 + 0.005);
  gain.gain.exponentialRampToValueAtTime(0.0001, t1);

  osc.connect(gain).connect(ac.destination);
  osc.start(t0);
  osc.stop(t1 + 0.01);
}

/** Two-tone "critical" beep — distinct, still subtle. */
export function beepCritical() {
  beep({ freq: 1180, durationMs: 70, volume: 0.07 });
  setTimeout(() => beep({ freq: 740, durationMs: 110, volume: 0.07 }), 80);
}
