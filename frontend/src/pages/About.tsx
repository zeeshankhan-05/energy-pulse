import { Zap, Database, Brain, Bell, Globe } from "lucide-react";

const TECH_STACK = [
  { label: "FastAPI", icon: Globe },
  { label: "PostgreSQL", icon: Database },
  { label: "Celery + Redis", icon: Zap },
  { label: "Anomaly Detection", icon: Brain },
  { label: "SendGrid + Slack", icon: Bell },
];

export default function About() {
  return (
    <div className="animate-fade-in max-w-2xl space-y-8">
      <div className="space-y-3">
        <h3 className="text-2xl font-bold tracking-wide text-text-primary">
          What is EnergyPulse?
        </h3>
        <p className="text-text-secondary leading-relaxed">
          EnergyPulse is a full-stack energy market intelligence platform that ingests live
          pricing data from the U.S. Energy Information Administration and state public utility
          commissions, detects statistical anomalies using rolling Z-score analysis, and delivers
          real-time alerts via email and Slack.
        </p>
      </div>

      <div className="space-y-3">
        <h4 className="text-sm font-bold tracking-widest uppercase text-text-muted">
          Tech Stack
        </h4>
        <div className="flex flex-wrap gap-2">
          {TECH_STACK.map(({ label, icon: Icon }) => (
            <div
              key={label}
              className="flex items-center gap-2 px-3 py-1.5 bg-bg-card border border-border
                         rounded-lg text-sm text-text-secondary"
            >
              <Icon className="w-3.5 h-3.5 text-accent" />
              {label}
            </div>
          ))}
        </div>
      </div>

      <div className="bg-bg-card border border-border rounded-xl p-5">
        <p className="text-xs font-mono text-text-muted">
          Built as a portfolio project demonstrating full-stack data engineering,
          real-time analytics, and multi-channel alert delivery.
        </p>
      </div>
    </div>
  );
}
