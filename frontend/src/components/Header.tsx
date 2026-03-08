import { useLocation } from "react-router-dom";
import { Clock } from "lucide-react";

const PAGE_TITLES: Record<string, string> = {
  "/dashboard": "Dashboard",
  "/alerts": "Alerts",
  "/about": "About",
};

export default function Header() {
  const location = useLocation();
  const pageTitle = PAGE_TITLES[location.pathname] ?? "EnergyPulse";

  return (
    <header className="sticky top-0 z-20 bg-bg-primary/80 backdrop-blur-md border-b border-border">
      <div className="flex items-center justify-between px-6 py-3.5">
        {/* Page title */}
        <h2 className="text-xl font-bold tracking-wide text-text-primary">
          {pageTitle}
        </h2>

        {/* Last updated timestamp */}
        <div className="flex items-center gap-2 text-text-muted">
          <Clock className="w-3.5 h-3.5" />
          <span className="text-xs font-mono tracking-wide">
            Last updated: <span className="text-text-secondary">—</span>
          </span>
        </div>
      </div>

      {/* Electric blue accent line */}
      <div className="h-px bg-gradient-to-r from-transparent via-accent/40 to-transparent" />
    </header>
  );
}
