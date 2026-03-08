import { Activity, ShieldAlert, Send } from "lucide-react";

const STATUS_CARDS = [
  {
    title: "Data Pipeline",
    description: "EIA API + state PUC scraping",
    status: "Operational",
    statusColor: "bg-success",
    icon: Activity,
    value: "—",
    label: "records ingested",
  },
  {
    title: "Anomaly Detection",
    description: "Z-score + threshold analysis",
    status: "Operational",
    statusColor: "bg-success",
    icon: ShieldAlert,
    value: "—",
    label: "alerts created",
  },
  {
    title: "Alert Delivery",
    description: "Email + Slack notifications",
    status: "Standby",
    statusColor: "bg-warning",
    icon: Send,
    value: "—",
    label: "delivered",
  },
];

export default function Dashboard() {
  return (
    <div className="animate-fade-in space-y-8">
      {/* Status card grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {STATUS_CARDS.map((card) => {
          const Icon = card.icon;
          return (
            <div
              key={card.title}
              className="bg-bg-card border border-border rounded-xl p-5 hover:border-border-hover
                         transition-all duration-300 hover:shadow-[0_0_20px_rgba(59,130,246,0.05)]"
            >
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-accent/10">
                  <Icon className="w-5 h-5 text-accent" />
                </div>
                <div className="flex items-center gap-1.5">
                  <span className={`w-2 h-2 rounded-full ${card.statusColor} animate-pulse-dot`} />
                  <span className="text-xs font-mono text-text-muted">{card.status}</span>
                </div>
              </div>

              <h3 className="text-base font-bold tracking-wide text-text-primary mb-0.5">
                {card.title}
              </h3>
              <p className="text-xs text-text-muted mb-4">{card.description}</p>

              <div className="pt-3 border-t border-border">
                <span className="text-2xl font-bold font-mono text-text-primary">{card.value}</span>
                <span className="text-xs text-text-muted ml-2">{card.label}</span>
              </div>
            </div>
          );
        })}
      </div>

      {/* Placeholder section */}
      <div className="bg-bg-card border border-border rounded-xl p-8 text-center">
        <p className="text-text-muted text-sm">
          Charts and live data feeds will appear here once connected to the API.
        </p>
      </div>
    </div>
  );
}
