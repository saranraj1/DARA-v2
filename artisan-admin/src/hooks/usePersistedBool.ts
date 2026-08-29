import { useCallback, useEffect, useRef, useState } from "react";

/** Persistent boolean preference in localStorage. SSR-safe (defaults until mount). */
export function usePersistedBool(key: string, initial: boolean) {
  const [value, setValueRaw] = useState<boolean>(() => {
    if (typeof window === "undefined") return initial;
    try {
      const raw = localStorage.getItem(key);
      if (raw === null) return initial;
      return raw === "1" || raw === "true";
    } catch { return initial; }
  });
  // Keep a ref so the setter is stable.
  const keyRef = useRef(key);
  useEffect(() => { keyRef.current = key; }, [key]);

  const setValue = useCallback((v: boolean | ((prev: boolean) => boolean)) => {
    setValueRaw(prev => {
      const next = typeof v === "function" ? (v as (p: boolean) => boolean)(prev) : v;
      try { localStorage.setItem(keyRef.current, next ? "1" : "0"); } catch { }
      return next;
    });
  }, []);

  return [value, setValue] as const;
}
