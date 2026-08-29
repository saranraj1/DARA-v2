import { Sidebar } from "@/components/Sidebar";
import { Outlet } from "react-router-dom";

export function AppLayout() {
  return (
    <div className="flex min-h-screen w-full bg-background">
      <Sidebar />
      <main className="flex-1 min-w-0 scanlines">
        <Outlet />
      </main>
    </div>
  );
}
