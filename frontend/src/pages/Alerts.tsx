import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  XCircle,
  Shield,
  Trash2,
  Plus,
  CheckCircle,
} from "lucide-react";

import {
  fetchAlerts,
  fetchAlertConfigs,
  fetchRegions,
  createAlertConfig,
  deleteAlertConfig,
} from "../api/api";
import type { AlertItem, AlertConfigItem } from "../api/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

type FuelType = "electricity" | "natural_gas";

const FUEL_OPTIONS: { label: string; value: FuelType }[] = [
  { label: "Electricity", value: "electricity" },
  { label: "Natural Gas", value: "natural_gas" },
];

const SEVERITY_STYLES: Record<
  string,
  { border: string; badge: string; icon: typeof AlertTriangle }
> = {
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Convert ISO timestamp to relative string, e.g. "2 hours ago" */
function timeAgo(iso: string): string {
  const seconds = Math.floor(
    (Date.now() - new Date(iso).getTime()) / 1000
  );
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

/** Format fuel_type for display: "electricity" → "Electricity" */
function fuelLabel(ft: string): string {
  return ft === "natural_gas" ? "Natural Gas" : "Electricity";
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

function SkeletonAlertCard() {
  return (
    <div className="bg-bg-card border border-border rounded-lg p-4 animate-pulse">
      <div className="flex items-start gap-3">
        <div className="w-5 h-5 bg-gray-700 rounded" />
        <div className="flex-1 space-y-2">
          <div className="h-3 w-40 bg-gray-700 rounded" />
          <div className="h-4 w-full bg-gray-700 rounded" />
        </div>
        <div className="h-5 w-16 bg-gray-700 rounded" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

function Toast({ message, onClose }: { message: string; onClose: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 3000);
    return () => clearTimeout(timer);
  }, [onClose]);

  return (
    <div className="fixed bottom-6 right-6 z-50 animate-fade-in flex items-center gap-2
                    bg-success/90 text-white px-4 py-3 rounded-lg shadow-lg">
      <CheckCircle className="w-4 h-4" />
      <span className="text-sm font-semibold">{message}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pill
// ---------------------------------------------------------------------------

function Pill<T extends string | number>({
  label,
  value,
  active,
  onClick,
}: {
  label: string;
  value: T;
  active: boolean;
  onClick: (v: T) => void;
}) {
  return (
    <button
      onClick={() => onClick(value)}
      className={`
        px-3 py-1.5 rounded-lg text-sm font-semibold tracking-wide transition-all duration-200
        ${active
          ? "bg-accent text-white shadow-[0_0_12px_rgba(59,130,246,0.3)]"
          : "bg-bg-card border border-border text-text-secondary hover:text-text-primary hover:border-border-hover"
        }
      `}
    >
      {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Alerts Page
// ---------------------------------------------------------------------------

export default function Alerts() {
  // ---- Section 1 state: anomaly feed ----
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [alertsLoading, setAlertsLoading] = useState(true);
  const [alertsError, setAlertsError] = useState<string | null>(null);

  // ---- Section 2 state: config form ----
  const [regions, setRegions] = useState<string[]>([]);
  const [configs, setConfigs] = useState<AlertConfigItem[]>([]);
  const [formRegion, setFormRegion] = useState("");
  const [formFuel, setFormFuel] = useState<FuelType>("electricity");
  const [formThreshold, setFormThreshold] = useState(15);
  const [formEmail, setFormEmail] = useState("");
  const [formSlack, setFormSlack] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  // Stable refs for interval cleanup
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ---- Load alerts ----
  const loadAlerts = useCallback(async () => {
    try {
      const data = await fetchAlerts();
      setAlerts(data);
      setAlertsError(null);
    } catch (err: unknown) {
      setAlertsError(err instanceof Error ? err.message : "Failed to fetch alerts");
    } finally {
      setAlertsLoading(false);
    }
  }, []);

  // Fetch alerts on mount + auto-refresh every 60s with proper cleanup
  useEffect(() => {
    loadAlerts();
    intervalRef.current = setInterval(loadAlerts, 60_000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [loadAlerts]);

  // ---- Load regions + configs on mount ----
  useEffect(() => {
    fetchRegions().then((data) => {
      setRegions(data);
      if (data.length > 0) setFormRegion(data[0]);
    });
    fetchAlertConfigs().then(setConfigs).catch(() => {});
  }, []);

  // ---- Form submit ----
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formRegion) return;
    setSubmitting(true);
    try {
      await createAlertConfig({
        region: formRegion,
        fuel_type: formFuel,
        threshold_pct: formThreshold,
        email: formEmail || undefined,
        slack_webhook: formSlack || undefined,
      });
      setToast("Alert config saved!");
      setFormEmail("");
      setFormSlack("");
      setFormThreshold(15);
      // Refetch configs
      const updated = await fetchAlertConfigs();
      setConfigs(updated);
    } catch (err: unknown) {
      setToast(err instanceof Error ? err.message : "Failed to save config");
    } finally {
      setSubmitting(false);
    }
  };

  // ---- Delete config ----
  const handleDelete = async (id: string) => {
    try {
      await deleteAlertConfig(id);
      setConfigs((prev) => prev.filter((c) => c.id !== id));
    } catch {
      // Silent fail — config list will self-correct on next fetch
    }
  };

  return (
    <div className="animate-fade-in space-y-10">
      {/* ================================================================
          SECTION 1 — Recent Anomalies
          ================================================================ */}
      <section>
        <h3 className="text-lg font-bold tracking-wide text-text-primary mb-4">
          Recent Anomalies
        </h3>

        {alertsLoading ? (
          <div className="space-y-3">
            <SkeletonAlertCard />
            <SkeletonAlertCard />
            <SkeletonAlertCard />
          </div>
        ) : alertsError ? (
          <div className="bg-bg-card border border-critical/30 rounded-xl p-6 text-center">
            <p className="text-critical text-sm">{alertsError}</p>
          </div>
        ) : alerts.length === 0 ? (
          <div className="bg-bg-card border border-border rounded-xl p-8 text-center">
            <Shield className="w-8 h-8 text-success mx-auto mb-3" />
            <p className="text-text-secondary text-sm">
              No anomalies detected — markets look stable.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {alerts.map((alert) => {
              const style =
                SEVERITY_STYLES[alert.severity] ?? SEVERITY_STYLES.warning;
              const Icon = style.icon;
              // deviation_pct is a decimal from the API (0.18 = 18%)
              const deviationDisplay = (alert.deviation_pct * 100).toFixed(1);

              return (
                <div
                  key={alert.id}
                  className={`bg-bg-card border border-border ${style.border} border-l-[3px]
                              rounded-lg p-4 hover:border-border-hover transition-colors`}
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex items-start gap-3 flex-1 min-w-0">
                      <Icon className="w-5 h-5 mt-0.5 shrink-0" />
                      <div className="min-w-0">
                        {/* Badges */}
                        <div className="flex flex-wrap items-center gap-2 mb-1.5">
                          <span
                            className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded ${style.badge}`}
                          >
                            {alert.severity}
                          </span>
                          <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-accent/10 text-accent">
                            {alert.region} · {fuelLabel(alert.fuel_type)}
                          </span>
                        </div>

                        {/* Message */}
                        <p className="text-sm text-text-secondary mb-2">
                          {alert.message}
                        </p>

                        {/* Price comparison */}
                        <div className="flex items-center gap-4 text-xs font-mono">
                          <span className="text-text-primary">
                            Price:{" "}
                            <span className="text-accent font-bold">
                              ${alert.current_price.toFixed(4)}
                            </span>
                          </span>
                          <span className="text-text-muted">vs</span>
                          <span className="text-text-primary">
                            Avg:{" "}
                            <span className="text-text-secondary">
                              ${alert.rolling_avg_price.toFixed(4)}
                            </span>
                          </span>
                        </div>
                      </div>
                    </div>

                    {/* Right: deviation + timestamp */}
                    <div className="text-right shrink-0">
                      <span className="text-lg font-bold font-mono text-text-primary">
                        +{deviationDisplay}%
                      </span>
                      <p className="text-[10px] text-text-muted mt-0.5">
                        {timeAgo(alert.triggered_at)}
                      </p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* ================================================================
          SECTION 2 — Alert Configuration
          ================================================================ */}
      <section>
        <h3 className="text-lg font-bold tracking-wide text-text-primary mb-4">
          Create Alert
        </h3>

        <form
          onSubmit={handleSubmit}
          className="bg-bg-card border border-border rounded-xl p-6 space-y-5"
        >
          {/* Row 1: Region + Fuel type */}
          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1.5">
              <label className="text-xs text-text-muted uppercase tracking-wider font-semibold">
                Region
              </label>
              <select
                value={formRegion}
                onChange={(e) => setFormRegion(e.target.value)}
                className="bg-bg-primary border border-border text-text-primary rounded-lg px-3 py-2
                           text-sm font-semibold focus:outline-none focus:border-accent
                           appearance-none cursor-pointer"
              >
                {regions.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs text-text-muted uppercase tracking-wider font-semibold">
                Fuel Type
              </label>
              <div className="flex gap-2">
                {FUEL_OPTIONS.map((opt) => (
                  <Pill
                    key={opt.value}
                    label={opt.label}
                    value={opt.value}
                    active={formFuel === opt.value}
                    onClick={(v) => setFormFuel(v as FuelType)}
                  />
                ))}
              </div>
            </div>
          </div>

          {/* Row 2: Threshold */}
          <div className="space-y-1.5">
            <label className="text-xs text-text-muted uppercase tracking-wider font-semibold">
              Threshold %
            </label>
            <input
              type="number"
              min={5}
              max={50}
              value={formThreshold}
              onChange={(e) => setFormThreshold(Number(e.target.value))}
              className="bg-bg-primary border border-border text-text-primary rounded-lg px-3 py-2
                         text-sm font-mono w-24 focus:outline-none focus:border-accent"
            />
            <p className="text-[11px] text-text-muted">
              Alert when price deviates above this % from rolling average (5–50%)
            </p>
          </div>

          {/* Row 3: Email + Slack */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-xs text-text-muted uppercase tracking-wider font-semibold">
                Email
              </label>
              <input
                type="email"
                placeholder="alerts@example.com"
                value={formEmail}
                onChange={(e) => setFormEmail(e.target.value)}
                className="w-full bg-bg-primary border border-border text-text-primary rounded-lg px-3 py-2
                           text-sm focus:outline-none focus:border-accent placeholder:text-text-muted"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs text-text-muted uppercase tracking-wider font-semibold">
                Slack Webhook{" "}
                <span className="text-text-muted font-normal normal-case">(optional)</span>
              </label>
              <input
                type="url"
                placeholder="https://hooks.slack.com/services/..."
                value={formSlack}
                onChange={(e) => setFormSlack(e.target.value)}
                className="w-full bg-bg-primary border border-border text-text-primary rounded-lg px-3 py-2
                           text-sm focus:outline-none focus:border-accent placeholder:text-text-muted"
              />
            </div>
          </div>

          {/* Submit */}
          <button
            type="submit"
            disabled={submitting || !formRegion}
            className="inline-flex items-center gap-2 px-5 py-2.5 bg-accent text-white
                       rounded-lg text-sm font-bold tracking-wide
                       hover:bg-accent/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Plus className="w-4 h-4" />
            {submitting ? "Saving…" : "Save Alert Config"}
          </button>
        </form>

        {/* ---- Saved configs list ---- */}
        {configs.length > 0 && (
          <div className="mt-6 space-y-2">
            <h4 className="text-xs text-text-muted uppercase tracking-wider font-semibold mb-3">
              Saved Configs
            </h4>
            {configs.map((cfg) => (
              <div
                key={cfg.id}
                className="flex items-center justify-between bg-bg-card border border-border
                           rounded-lg px-4 py-3"
              >
                <div className="flex items-center gap-3 text-sm">
                  <span className="font-bold text-text-primary">{cfg.region}</span>
                  <span className="text-text-muted">·</span>
                  <span className="text-text-secondary">
                    {fuelLabel(cfg.fuel_type)}
                  </span>
                  <span className="text-text-muted">·</span>
                  <span className="font-mono text-accent">{cfg.threshold_pct}%</span>
                  {cfg.email && (
                    <>
                      <span className="text-text-muted">·</span>
                      <span className="text-text-muted text-xs truncate max-w-[160px]">
                        {cfg.email}
                      </span>
                    </>
                  )}
                </div>
                <button
                  onClick={() => handleDelete(cfg.id)}
                  className="text-text-muted hover:text-critical transition-colors p-1"
                  title="Delete config"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ---- Toast ---- */}
      {toast && <Toast message={toast} onClose={() => setToast(null)} />}
    </div>
  );
}
