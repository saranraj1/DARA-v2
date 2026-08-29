import { useCallback, useEffect, useRef, useState } from "react";

interface Persisted {
  enabled: boolean;
  paused: boolean;
}

interface Options<T> {
  storageKey: string;
  /** Initial seed rows. Newest first. */
  seed: T[];
  /** Generate the next synthetic row (called on each tick when live & not paused). */
  generate: (existing: T[]) => T;
  /** Fetch latest live rows from backend. If provided, overrides synthetic generation. */
  fetchLatest?: () => Promise<T[]>;
  /** Stable id getter for highlight tracking. */
  getId: (row: T) => string;
  /** ms between synthetic emits */
  intervalMs?: number;
  /** how long a row stays "fresh" (highlighted) */
  freshMs?: number;
  /** cap so memory doesn't grow forever */
  maxRows?: number;
  /** Externally-driven pause (e.g. detail panel open). Not persisted. */
  externallyPaused?: boolean;
  /** Fired for every newly-arrived row (post-render schedule). Use for side-effects. */
  onArrive?: (row: T) => void;
}

export interface LiveTail<T> {
  rows: T[];
  freshIds: Set<string>;
  enabled: boolean;
  paused: boolean;          // user-initiated
  effectivePaused: boolean; // user OR external
  buffered: number;
  /** Per-minute arrival counts over the last `rateWindowMins` minutes. Oldest → newest. */
  rateSeries: number[];
  setEnabled: (v: boolean) => void;
  setPaused: (v: boolean) => void;
  flush: () => void;
}

const RATE_WINDOW_MINS = 6;

function load(key: string): Persisted {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return { enabled: true, paused: false };
    return { enabled: true, paused: false, ...JSON.parse(raw) };
  } catch {
    return { enabled: true, paused: false };
  }
}
function save(key: string, p: Persisted) {
  try { localStorage.setItem(key, JSON.stringify(p)); } catch {}
}

export function useLiveTail<T>({
  storageKey,
  seed,
  generate,
  fetchLatest,
  getId,
  intervalMs = 4000,
  freshMs = 2500,
  maxRows = 500,
  externallyPaused = false,
  onArrive,
}: Options<T>): LiveTail<T> {
  const persisted = load(storageKey);
  const [enabled, setEnabledRaw] = useState(persisted.enabled);
  const [paused, setPausedRaw] = useState(persisted.paused);
  const [rows, setRows] = useState<T[]>(seed);
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());

  // Buffer accumulates rows generated while paused so user can flush them.
  const bufferRef = useRef<T[]>([]);
  const [buffered, setBuffered] = useState(0);

  // Per-minute rate tracking (timestamps of arrivals)
  const arrivalTsRef = useRef<number[]>([]);
  const [rateSeries, setRateSeries] = useState<number[]>(() => Array(RATE_WINDOW_MINS).fill(0));

  // Latest callback ref — avoids re-creating the interval when caller passes a new fn.
  const onArriveRef = useRef(onArrive);
  useEffect(() => { onArriveRef.current = onArrive; }, [onArrive]);
  const generateRef = useRef(generate);
  useEffect(() => { generateRef.current = generate; }, [generate]);
  const getIdRef = useRef(getId);
  useEffect(() => { getIdRef.current = getId; }, [getId]);
  const fetchLatestRef = useRef(fetchLatest);
  useEffect(() => { fetchLatestRef.current = fetchLatest; }, [fetchLatest]);

  const effectivePaused = paused || externallyPaused;
  const effectivePausedRef = useRef(effectivePaused);
  useEffect(() => { effectivePausedRef.current = effectivePaused; }, [effectivePaused]);

  const setEnabled = useCallback((v: boolean) => {
    setEnabledRaw(v);
    save(storageKey, { enabled: v, paused });
  }, [storageKey, paused]);

  const setPaused = useCallback((v: boolean) => {
    setPausedRaw(v);
    save(storageKey, { enabled, paused: v });
  }, [storageKey, enabled]);

  // Recompute the rate series every ~10s from the timestamp ring.
  useEffect(() => {
    const recompute = () => {
      const now = Date.now();
      const cutoff = now - RATE_WINDOW_MINS * 60_000;
      arrivalTsRef.current = arrivalTsRef.current.filter(t => t >= cutoff);
      const buckets = Array(RATE_WINDOW_MINS).fill(0);
      for (const t of arrivalTsRef.current) {
        const ageMs = now - t;
        const idx = RATE_WINDOW_MINS - 1 - Math.floor(ageMs / 60_000);
        if (idx >= 0 && idx < RATE_WINDOW_MINS) buckets[idx]++;
      }
      setRateSeries(buckets);
    };
    recompute();
    const t = setInterval(recompute, 10_000);
    return () => clearInterval(t);
  }, []);

  // Generate new rows or fetch latest rows on tick.
  useEffect(() => {
    if (!enabled) return;

    const tick = async () => {
      if (fetchLatestRef.current) {
        try {
          const fetched = await fetchLatestRef.current();
          if (!fetched || fetched.length === 0) return;

          setRows(current => {
            const allExisting = [...bufferRef.current, ...current];
            const knownIds = new Set(allExisting.map(getIdRef.current));

            // Filter to only new rows
            const newRows = fetched.filter(row => !knownIds.has(getIdRef.current(row)));
            if (newRows.length === 0) return current;

            newRows.forEach(row => {
              arrivalTsRef.current.push(Date.now());
              try { onArriveRef.current?.(row); } catch {}
            });

            if (effectivePausedRef.current) {
              bufferRef.current = [...newRows, ...bufferRef.current];
              setBuffered(bufferRef.current.length);
              return current;
            }

            const newIds = newRows.map(getIdRef.current);
            setFreshIds(prev => {
              const n = new Set(prev);
              newIds.forEach(id => n.add(id));
              return n;
            });
            window.setTimeout(() => {
              setFreshIds(prev => {
                const n = new Set(prev);
                newIds.forEach(id => n.delete(id));
                return n;
              });
            }, freshMs);

            const merged = [...newRows, ...current];
            return merged.length > maxRows ? merged.slice(0, maxRows) : merged;
          });
        } catch (error) {
          console.error("useLiveTail fetchLatest failed:", error);
        }
      } else {
        setRows(current => {
          const visible = [...bufferRef.current, ...current];
          const next = generateRef.current(visible);
          // Track arrival regardless of pause state — rate reflects backend volume.
          arrivalTsRef.current.push(Date.now());
          // Side-effect (beep, etc.) for every new row.
          try { onArriveRef.current?.(next); } catch { /* ignore */ }

          if (effectivePausedRef.current) {
            bufferRef.current = [next, ...bufferRef.current];
            setBuffered(bufferRef.current.length);
            return current; // unchanged on screen
          }
          const id = getIdRef.current(next);
          setFreshIds(prev => {
            const n = new Set(prev);
            n.add(id);
            return n;
          });
          window.setTimeout(() => {
            setFreshIds(prev => {
              if (!prev.has(id)) return prev;
              const n = new Set(prev);
              n.delete(id);
              return n;
            });
          }, freshMs);
          const merged = [next, ...current];
          return merged.length > maxRows ? merged.slice(0, maxRows) : merged;
        });
      }
    };

    if (fetchLatestRef.current) {
      tick();
    }
    const t = setInterval(tick, intervalMs);
    return () => clearInterval(t);
  }, [enabled, intervalMs, freshMs, maxRows]);

  const flush = useCallback(() => {
    if (bufferRef.current.length === 0) return;
    const incoming = bufferRef.current;
    bufferRef.current = [];
    setBuffered(0);
    setRows(current => {
      const merged = [...incoming, ...current];
      return merged.length > maxRows ? merged.slice(0, maxRows) : merged;
    });
    const ids = incoming.map(r => getIdRef.current(r));
    setFreshIds(prev => {
      const n = new Set(prev);
      ids.forEach(id => n.add(id));
      return n;
    });
    window.setTimeout(() => {
      setFreshIds(prev => {
        const n = new Set(prev);
        ids.forEach(id => n.delete(id));
        return n;
      });
    }, freshMs);
  }, [freshMs, maxRows]);

  return {
    rows, freshIds, enabled, paused, effectivePaused, buffered,
    rateSeries, setEnabled, setPaused, flush,
  };
}
