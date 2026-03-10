import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AreaChart,
  Area,
  LineChart,
  Line,
  Legend,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import { RefreshCw } from "lucide-react";

import { fetchPrices, fetchRegions } from "../api/api";
import type { PricePoint } from "../api/api";
import MarketSummaryCard from "../components/MarketSummary";
import DataTable from "../components/DataTable";
import PipelineStats from "../components/PipelineStats";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// recharts v3 removed `payload` from TooltipProps — use a local interface instead
interface TooltipCustomProps {
  active?: boolean;
  payload?: any[];
  label?: string;
}

type FuelType = "electricity" | "natural_gas";
type Months = 3 | 6 | 12;

const FUEL_OPTIONS: { label: string; value: FuelType }[] = [
  { label: "Electricity", value: "electricity" },
  { label: "Natural Gas", value: "natural_gas" },
];

const MONTH_OPTIONS: Months[] = [3, 6, 12];

const COMPARE_STATES = ["IL", "TX", "OH", "CA", "NY", "AZ"];
const COMPARE_COLORS: Record<string, string> = {
  IL: "#3b82f6", // blue
  TX: "#ef4444", // red
  OH: "#10b981", // emerald
  CA: "#f59e0b", // amber
  NY: "#8b5cf6", // violet
  AZ: "#f472b6", // pink
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** "2024-11" → "Nov '24" */
function formatPeriod(period: string): string {
  if (!period) return "";
  const [year, month] = period.split("-");
  const date = new Date(Number(year), Number(month) - 1);
  const monthLabel = date.toLocaleString("en-US", { month: "short" });
  return `${monthLabel} '${year.slice(2)}`;
}

// ---------------------------------------------------------------------------
// Custom Tooltip
// ---------------------------------------------------------------------------

function PriceTooltip({ active, payload, label }: TooltipCustomProps) {
  if (!active || !payload?.length) return null;
  const isCompare = payload.length > 0 && payload[0].payload && !('price' in payload[0].payload);

  if (isCompare) {
    const data = payload[0].payload;
    return (
      <div className="bg-[#1e2330] border border-border rounded-lg px-3 py-2 shadow-xl">
        <p className="text-xs text-text-muted mb-1">{formatPeriod(data.period || label || "")}</p>
        {payload.map((p: any) => (
          <p key={p.dataKey} className="text-sm font-mono font-bold" style={{ color: p.stroke || p.fill }}>
            {p.name}: ${Number(p.value).toFixed(4)}{" "}
            <span className="text-text-muted font-normal text-xs">{data.unit || "USD"}</span>
          </p>
        ))}
      </div>
    );
  }

  const data = payload[0].payload as PricePoint;
  return (
    <div className="bg-[#1e2330] border border-border rounded-lg px-3 py-2 shadow-xl">
      <p className="text-xs text-text-muted mb-1">{formatPeriod(data.period)}</p>
      <p className="text-sm font-mono font-bold text-text-primary">
        ${(data.price || 0).toFixed(4)}{" "}
        <span className="text-text-muted font-normal">{data.unit}</span>
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton components
// ---------------------------------------------------------------------------

function SkeletonCard() {
  return (
    <div className="bg-bg-card border border-border rounded-xl p-5 animate-pulse">
      <div className="h-3 w-24 bg-gray-700 rounded mb-4" />
      <div className="h-7 w-32 bg-gray-700 rounded mb-2" />
      <div className="h-3 w-20 bg-gray-700 rounded" />
    </div>
  );
}

function SkeletonChart() {
  return (
    <div className="bg-bg-card border border-border rounded-xl p-6 animate-pulse">
      <div className="h-[340px] bg-gray-800 rounded-lg" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pill button helper
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
        px-3.5 py-1.5 rounded-lg text-sm font-semibold tracking-wide transition-all duration-200
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
// Dashboard
// ---------------------------------------------------------------------------

export default function Dashboard() {
  // State
  const [regions, setRegions] = useState<string[]>([]);
  const [selectedRegion, setSelectedRegion] = useState<string>("");
  const [fuelType, setFuelType] = useState<FuelType>("electricity");
  const [months, setMonths] = useState<Months>(6);
  const [prices, setPrices] = useState<PricePoint[]>([]);
  
  // Compare Mode State
  const [isCompareMode, setIsCompareMode] = useState(false);
  const [compareRegions, setCompareRegions] = useState<string[]>(["IL", "TX", "NY"]);
  const [compareData, setCompareData] = useState<any[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch regions once on mount
  useEffect(() => {
    fetchRegions()
      .then((data) => {
        setRegions(data);
        if (data.length > 0) setSelectedRegion(data[0]);
      })
      .catch((err) => setError(err.message));
  }, []);

  // Fetch prices when filters change
  const loadPrices = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (isCompareMode) {
        if (compareRegions.length === 0) {
          setCompareData([]);
          setLoading(false);
          return;
        }
        const promises = compareRegions.map(r => fetchPrices({
          region: r,
          fuelType,
          months,
        }));
        const results = await Promise.all(promises);
        
        const mergedMap = new Map<string, any>();
        compareRegions.forEach((region, i) => {
          results[i].forEach(p => {
            if (!mergedMap.has(p.period)) {
              mergedMap.set(p.period, { period: p.period, unit: p.unit });
            }
            const entry = mergedMap.get(p.period);
            entry[region] = p.price;
          });
        });
        const merged = Array.from(mergedMap.values()).sort((a, b) => a.period.localeCompare(b.period));
        setCompareData(merged);
        setPrices([]); // Clear single prices
      } else {
        if (!selectedRegion) {
            setLoading(false);  
            return;
        }
        const data = await fetchPrices({
          region: selectedRegion,
          fuelType,
          months,
        });
        setPrices(data);
        setCompareData([]);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to fetch prices";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [isCompareMode, selectedRegion, compareRegions, fuelType, months]);

  useEffect(() => {
    loadPrices();
  }, [loadPrices]);

  // Computed stats
  const stats = useMemo(() => {
    if (prices.length === 0) return null;
    const first = prices[0];
    const last = prices[prices.length - 1];
    const allPrices = prices.map((p) => p.price);
    const high = Math.max(...allPrices);
    const low = Math.min(...allPrices);
    const avg = allPrices.reduce((s, v) => s + v, 0) / allPrices.length;
    const pctChange = ((last.price - first.price) / first.price) * 100;

    return {
      currentPrice: last.price,
      unit: last.unit,
      currentPeriod: last.period,
      pctChange,
      firstPeriod: first.period,
      lastPeriod: last.period,
      high,
      low,
      avg,
    };
  }, [prices]);

  return (
    <div className="animate-fade-in space-y-6">
      {/* ---- Filter bar ---- */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        {/* Mode & Region Selector */}
        <div className="flex items-center gap-4 flex-wrap">
          {/* Compare Toggle */}
          <div className="flex gap-1 bg-bg-card p-1 border border-border rounded-lg">
            <button
              onClick={() => setIsCompareMode(false)}
              className={`px-3 py-1.5 text-sm font-semibold rounded-md transition-colors ${
                !isCompareMode ? "bg-accent text-white" : "text-text-secondary hover:text-text-primary"
              }`}
            >
              Single
            </button>
            <button
              onClick={() => setIsCompareMode(true)}
              className={`px-3 py-1.5 text-sm font-semibold rounded-md transition-colors ${
                isCompareMode ? "bg-accent text-white" : "text-text-secondary hover:text-text-primary"
              }`}
            >
              Compare
            </button>
          </div>

          {!isCompareMode ? (
            <select
              value={selectedRegion}
              onChange={(e) => setSelectedRegion(e.target.value)}
              className="bg-bg-card border border-border text-text-primary rounded-lg px-3 py-2
                         text-sm font-semibold focus:outline-none focus:border-accent
                         appearance-none cursor-pointer min-w-32"
            >
              {Array.from(new Set([...regions, "AZ"])).sort().map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          ) : (
            <div className="flex items-center gap-4 pl-2">
              {COMPARE_STATES.map(state => (
                <label key={state} className="flex items-center gap-2 cursor-pointer text-sm font-semibold text-text-primary">
                  <input
                    type="checkbox"
                    checked={compareRegions.includes(state)}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setCompareRegions(prev => [...prev, state]);
                      } else {
                        setCompareRegions(prev => prev.filter(r => r !== state));
                      }
                    }}
                    className="w-4 h-4 cursor-pointer rounded border-border"
                    style={{ accentColor: COMPARE_COLORS[state] }}
                  />
                  <span style={{ color: COMPARE_COLORS[state] }}>{state}</span>
                </label>
              ))}
            </div>
          )}
        </div>

        <div className="flex gap-4 flex-wrap">
          {/* Fuel type pills */}
          <div className="flex gap-2">
            {FUEL_OPTIONS.map((opt) => (
              <Pill
                key={opt.value}
                label={opt.label}
                value={opt.value}
                active={fuelType === opt.value}
                onClick={(v) => setFuelType(v as FuelType)}
              />
            ))}
          </div>

          {/* Time range pills */}
          <div className="flex gap-2">
            {MONTH_OPTIONS.map((m) => (
              <Pill
                key={m}
                label={`${m}M`}
                value={m}
                active={months === m}
                onClick={(v) => setMonths(v as Months)}
              />
            ))}
          </div>
        </div>
      </div>
      
      {/* ---- AI Market Summary ---- */}
      {!isCompareMode && <MarketSummaryCard region={selectedRegion} />}

      {/* ---- Error state ---- */}
      {error && !loading && (
        <div className="bg-bg-card border border-critical/30 rounded-xl p-8 text-center">
          <p className="text-critical text-sm mb-4">{error}</p>
          <button
            onClick={loadPrices}
            className="inline-flex items-center gap-2 px-4 py-2 bg-accent text-white
                       rounded-lg text-sm font-semibold hover:bg-accent/80 transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
            Retry
          </button>
        </div>
      )}

      {/* ---- Stat cards ---- */}
      {!isCompareMode && loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : !isCompareMode && stats ? (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {/* Current Price */}
          <div className="bg-bg-card border border-border rounded-xl p-5">
            <p className="text-xs text-text-muted mb-1 uppercase tracking-wider font-semibold">
              Current Price
            </p>
            <p className="text-2xl font-bold font-mono text-text-primary">
              ${stats.currentPrice.toFixed(4)}
              <span className="text-sm text-text-muted ml-1.5 font-normal">
                {stats.unit}
              </span>
            </p>
            <p className="text-xs font-mono text-text-muted mt-1">
              {formatPeriod(stats.currentPeriod)}
            </p>
          </div>

          {/* Period Change */}
          <div className="bg-bg-card border border-border rounded-xl p-5">
            <p className="text-xs text-text-muted mb-1 uppercase tracking-wider font-semibold">
              Period Change
            </p>
            <p
              className={`text-2xl font-bold font-mono ${
                stats.pctChange >= 0 ? "text-critical" : "text-success"
              }`}
            >
              {stats.pctChange >= 0 ? "↑" : "↓"}{" "}
              {Math.abs(stats.pctChange).toFixed(1)}%
            </p>
            <p className="text-xs font-mono text-text-muted mt-1">
              {formatPeriod(stats.firstPeriod)} → {formatPeriod(stats.lastPeriod)}
            </p>
          </div>

          {/* High / Low */}
          <div className="bg-bg-card border border-border rounded-xl p-5">
            <p className="text-xs text-text-muted mb-1 uppercase tracking-wider font-semibold">
              Range High / Low
            </p>
            <div className="flex items-baseline gap-3">
              <div>
                <span className="text-lg font-bold font-mono text-critical">
                  ${stats.high.toFixed(4)}
                </span>
                <span className="text-[10px] text-text-muted ml-1">HIGH</span>
              </div>
              <span className="text-text-muted">/</span>
              <div>
                <span className="text-lg font-bold font-mono text-success">
                  ${stats.low.toFixed(4)}
                </span>
                <span className="text-[10px] text-text-muted ml-1">LOW</span>
              </div>
            </div>
            <p className="text-xs font-mono text-text-muted mt-1">
              {stats.unit}
            </p>
          </div>
        </div>
      ) : null}

      {/* ---- Chart ---- */}
      {loading ? (
        <SkeletonChart />
      ) : (!isCompareMode && prices.length > 0 && stats) || (isCompareMode && compareData.length > 0) ? (
        <div className="bg-bg-card border border-border rounded-xl p-4 pt-6">
          <ResponsiveContainer width="100%" height={isCompareMode ? 400 : 340}>
            {isCompareMode ? (
              <LineChart data={compareData} margin={{ top: 5, right: 20, left: 10, bottom: 25 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis
                  dataKey="period"
                  tickFormatter={formatPeriod}
                  tick={{ fill: "#9ca3af", fontSize: 12 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tickFormatter={(v: number) => `$${v.toFixed(4)}`}
                  tick={{ fill: "#9ca3af", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  label={{
                    value: compareData[0]?.unit || "USD",
                    angle: -90,
                    position: "insideLeft",
                    fill: "#6b7280",
                    fontSize: 11,
                    dx: -5,
                  }}
                />
                <Tooltip content={<PriceTooltip />} />
                <Legend verticalAlign="top" height={36} iconType="circle" wrapperStyle={{ fontSize: 12 }} />
                {compareRegions.map(region => (
                  <Line
                    key={region}
                    type="monotone"
                    dataKey={region}
                    name={region}
                    stroke={COMPARE_COLORS[region]}
                    strokeWidth={3}
                    dot={{ r: 3, strokeWidth: 1 }}
                    activeDot={{ r: 6, strokeWidth: 0 }}
                    connectNulls
                  />
                ))}
              </LineChart>
            ) : (
              <AreaChart data={prices} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                <defs>
                  <linearGradient id="priceGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
                  </linearGradient>
                </defs>

                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />

                <XAxis
                  dataKey="period"
                  tickFormatter={formatPeriod}
                  tick={{ fill: "#9ca3af", fontSize: 12 }}
                  axisLine={false}
                  tickLine={false}
                />

                <YAxis
                  tickFormatter={(v: number) => `$${v.toFixed(4)}`}
                  tick={{ fill: "#9ca3af", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  label={{
                    value: stats?.unit,
                    angle: -90,
                    position: "insideLeft",
                    fill: "#6b7280",
                    fontSize: 11,
                    dx: -5,
                  }}
                />

                <Tooltip content={<PriceTooltip />} />

                {stats && (
                  <ReferenceLine
                    y={stats.avg}
                    stroke="#6b7280"
                    strokeDasharray="4 2"
                    label={{
                      value: "Avg",
                      fill: "#6b7280",
                      fontSize: 11,
                      position: "right",
                    }}
                  />
                )}

                <Area
                  type="monotone"
                  dataKey="price"
                  stroke="#3b82f6"
                  strokeWidth={2}
                  fill="url(#priceGradient)"
                  dot={false}
                  activeDot={{
                    r: 4,
                    stroke: "#3b82f6",
                    strokeWidth: 2,
                    fill: "#0f1117",
                  }}
                />
              </AreaChart>
            )}
          </ResponsiveContainer>
        </div>
      ) : !error ? (
        <div className="bg-bg-card border border-border rounded-xl p-8 text-center">
          <p className="text-text-muted text-sm">
            {isCompareMode
              ? compareRegions.length === 0
                ? "Select at least one state to compare."
                : "No price data available for the selected states. Try running the data pipeline first."
              : `No price data available for ${selectedRegion} / ${fuelType.replace("_", " ")}. Try running the data pipeline first.`}
          </p>
        </div>
      ) : null}

      {/* ---- Data Table ---- */}
      {!isCompareMode && <DataTable region={selectedRegion} />}

      {/* ---- Pipeline Stats ---- */}
      {!isCompareMode && <PipelineStats />}
    </div>
  );
}
