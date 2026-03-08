import { Bell, AlertTriangle, XCircle } from "lucide-react";

const SAMPLE_ALERTS = [
  {
    id: "1",
    region: "IL",
    fuelType: "Electricity",
    severity: "critical",
    deviation: "+58.3%",
    message: "Illinois electricity prices 58.3% above the 6-month average.",
    time: "2 hours ago",
  },
  {
    id: "2",
    region: "TX",
    fuelType: "Natural Gas",
    severity: "warning",
    deviation: "+22.1%",
    message: "Texas natural gas prices 22.1% above the 6-month average.",
    time: "6 hours ago",
  },
  {
    id: "3",
    region: "OH",
    fuelType: "Electricity",
    severity: "warning",
    deviation: "+17.8%",
    message: "Ohio electricity prices 17.8% above the 6-month average.",
    time: "1 day ago",
  },
];

const SEVERITY_STYLES: Record<string, { border: string; badge: string; icon: typeof Bell }> = {
  critical: {
    border: "border-l-critical",
    badge: "bg-critical/10 text-critical",
    icon: XCircle,
  },
  warning: {
    border: "border-l-warning",
    badge: "bg-warning/10 text-warning",
    icon: AlertTriangle,
  },
};

export default function Alerts() {
  return (
    <div className="animate-fade-in space-y-4">
      <p className="text-sm text-text-muted mb-6">
        Price anomaly alerts from the detection engine. Placeholder data shown below.
      </p>

      {SAMPLE_ALERTS.map((alert) => {
        const style = SEVERITY_STYLES[alert.severity] ?? SEVERITY_STYLES.warning;
        const Icon = style.icon;
        return (
          <div
            key={alert.id}
            className={`bg-bg-card border border-border ${style.border} border-l-[3px]
                        rounded-lg p-4 hover:border-border-hover transition-colors`}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-start gap-3">
                <Icon className="w-5 h-5 mt-0.5 shrink-0" />
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded ${style.badge}`}>
                      {alert.severity}
                    </span>
                    <span className="text-sm font-semibold text-text-primary">
                      {alert.region} · {alert.fuelType}
                    </span>
                  </div>
                  <p className="text-sm text-text-secondary">{alert.message}</p>
                </div>
              </div>
              <div className="text-right shrink-0">
                <span className="text-lg font-bold font-mono text-text-primary">{alert.deviation}</span>
                <p className="text-[10px] text-text-muted mt-0.5">{alert.time}</p>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
