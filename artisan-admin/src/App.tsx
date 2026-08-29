import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppLayout } from "./components/AppLayout";
import Dashboard from "./pages/Dashboard";
import Errors from "./pages/Errors";
import Fixes from "./pages/Fixes";
import Patterns from "./pages/Patterns";
import Strategy from "./pages/Strategy";
import Topology from "./pages/Topology";
import Audit from "./pages/Audit";
import NotFound from "./pages/NotFound";

const queryClient = new QueryClient();

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider delayDuration={150}>
      <Sonner
        theme="dark"
        position="bottom-right"
        toastOptions={{
          className: "!font-mono !text-[12px] !bg-surface-1 !border !border-border !rounded-none !text-foreground",
        }}
      />
      <BrowserRouter>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/errors" element={<Errors />} />
            <Route path="/fixes" element={<Fixes />} />
            <Route path="/patterns" element={<Patterns />} />
            <Route path="/strategy" element={<Strategy />} />
            <Route path="/topology" element={<Topology />} />
            <Route path="/audit" element={<Audit />} />
          </Route>
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
