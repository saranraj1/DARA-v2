import { NavLink, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

const nav = [
  { to: "/", label: "dashboard",   key: "01", short: "DSH" },
  { to: "/errors", label: "errors", key: "02", short: "ERR" },
  { to: "/fixes", label: "fixes / hitl", key: "03", short: "FIX" },
  { to: "/patterns", label: "patterns", key: "04", short: "PAT" },
  { to: "/strategy", label: "strategy", key: "05", short: "STR" },
  { to: "/topology", label: "topology", key: "06", short: "TOP" },
  { to: "/audit", label: "audit log", key: "07", short: "AUD" },
];

export function Sidebar() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const loc = useLocation();

  return (
    <aside className="w-[240px] shrink-0 border-r border-border bg-sidebar flex flex-col h-screen sticky top-0">
      {/* logo block */}
      <div className="px-4 py-4 border-b border-sidebar-border">
        <div className="flex items-center gap-2">
          <div className="relative">
            <div className="w-7 h-7 border border-primary/60 grid place-items-center bg-background">
              <span className="font-mono text-primary text-[11px] font-bold">D</span>
            </div>
            <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 bg-signal-ok rounded-full pulse-dot" />
          </div>
          <div className="leading-tight">
            <div className="font-mono text-[13px] font-bold text-foreground tracking-tight">DARA</div>
            <div className="label-mono text-[9px]">v0.4.2-rc1</div>
          </div>
        </div>
        <div className="mt-3 text-[10px] font-mono text-muted-foreground leading-tight">
          distributed architecture<br />root-cause agent
        </div>
      </div>

      {/* nav */}
      <nav className="flex-1 overflow-y-auto py-3 px-2">
        <div className="label-mono px-2 pb-2">// console</div>
        <ul className="space-y-0.5">
          {nav.map((n) => {
            const active = loc.pathname === n.to || (n.to !== "/" && loc.pathname.startsWith(n.to));
            return (
              <li key={n.to}>
                <NavLink
                  to={n.to}
                  className={cn(
                    "group flex items-center gap-3 px-2 py-1.5 font-mono text-[12px] border-l-2 transition-colors",
                    active
                      ? "border-primary bg-sidebar-accent text-primary"
                      : "border-transparent text-sidebar-foreground hover:border-border hover:bg-sidebar-accent/50 hover:text-foreground"
                  )}
                >
                  <span className={cn("text-[10px] tabular-nums w-5", active ? "text-primary" : "text-muted-foreground")}>
                    {n.key}
                  </span>
                  <span className={cn("text-[10px] tracking-widest w-8", active ? "text-primary" : "text-muted-foreground/70")}>
                    {n.short}
                  </span>
                  <span className="lowercase">{n.label}</span>
                  {active && <span className="ml-auto text-primary blink">_</span>}
                </NavLink>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* footer */}
      <div className="border-t border-sidebar-border p-3 space-y-2">
        <a href="#" className="block text-[10px] font-mono text-muted-foreground hover:text-primary transition-colors">
          → swagger / openapi.json
        </a>
        <div className="flex items-center justify-between text-[10px] font-mono text-muted-foreground tabular-nums">
          <span>uptime 14d 06h</span>
          <span>{time.toISOString().slice(11, 19)}Z</span>
        </div>
      </div>
    </aside>
  );
}
