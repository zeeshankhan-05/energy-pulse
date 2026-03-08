import { NavLink, useLocation } from "react-router-dom";
import { LayoutDashboard, Bell, Info, Zap } from "lucide-react";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/alerts", label: "Alerts", icon: Bell },
  { to: "/about", label: "About", icon: Info },
];

export default function Sidebar() {
  const location = useLocation();

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="hidden md:flex fixed left-0 top-0 bottom-0 w-64 flex-col bg-bg-sidebar border-r border-border z-30">
        {/* Logo */}
        <div className="flex items-center gap-2.5 px-6 py-5 border-b border-border">
          <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-accent/10">
            <Zap className="w-5 h-5 text-accent" />
          </div>
          <div>
            <h1 className="text-lg font-bold tracking-wide text-text-primary leading-none">
              EnergyPulse
            </h1>
            <p className="text-[11px] font-mono text-text-muted tracking-widest uppercase">
              Market Intel
            </p>
          </div>
        </div>

        {/* Nav links */}
        <nav className="flex-1 px-3 py-4 space-y-1">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => {
            const isActive = location.pathname === to;
            return (
              <NavLink
                key={to}
                to={to}
                className={`
                  group flex items-center gap-3 px-3 py-2.5 rounded-lg
                  text-[15px] font-semibold tracking-wide transition-all duration-200
                  ${isActive
                    ? "bg-accent/10 text-accent border-l-[3px] border-accent -ml-px"
                    : "text-text-secondary hover:text-text-primary hover:bg-bg-card"
                  }
                `}
              >
                <Icon
                  className={`w-[18px] h-[18px] transition-colors ${
                    isActive ? "text-accent" : "text-text-muted group-hover:text-text-secondary"
                  }`}
                />
                {label}
              </NavLink>
            );
          })}
        </nav>

        {/* Version footer */}
        <div className="px-6 py-4 border-t border-border">
          <p className="text-[11px] font-mono text-text-muted">v0.1.0 · Phase 3</p>
        </div>
      </aside>

      {/* Mobile bottom nav */}
      <nav className="md:hidden fixed bottom-0 left-0 right-0 bg-bg-sidebar border-t border-border z-30
                       flex items-center justify-around py-2 px-4 backdrop-blur-sm">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => {
          const isActive = location.pathname === to;
          return (
            <NavLink
              key={to}
              to={to}
              className={`
                flex flex-col items-center gap-1 px-3 py-1.5 rounded-lg transition-colors
                ${isActive ? "text-accent" : "text-text-muted"}
              `}
            >
              <Icon className="w-5 h-5" />
              <span className="text-[10px] font-semibold tracking-wider uppercase">{label}</span>
            </NavLink>
          );
        })}
      </nav>
    </>
  );
}
