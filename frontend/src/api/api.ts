/**
 * EnergyPulse API client — typed fetch wrappers for the FastAPI backend.
 */

const API_BASE = "http://localhost:8000";

// ---------------------------------------------------------------------------
// Types — Prices
// ---------------------------------------------------------------------------

export interface PricePoint {
  period: string;
  price: number;
  unit: string;
  source: string;
  region: string;
  fuel_type: string;
}

export interface PriceFilters {
  region: string;
  fuelType: string; // "electricity" | "natural_gas"
  months: number;
}

// ---------------------------------------------------------------------------
// Types — Alerts & Configs
// ---------------------------------------------------------------------------

export interface AlertItem {
  id: string;
  region: string;
  fuel_type: string;
  severity: string; // "warning" | "critical"
  deviation_pct: number; // decimal, e.g. 0.18 = 18%
  current_price: number;
  rolling_avg_price: number;
  message: string;
  triggered_at: string; // ISO 8601
  notified: boolean;
}

export interface AlertConfigItem {
  id: string;
  region: string;
  fuel_type: string;
  threshold_pct: number;
  email: string | null;
  slack_webhook: string | null;
  is_active: boolean;
}

export interface AlertConfigCreate {
  region: string;
  fuel_type: string;
  threshold_pct: number;
  email?: string;
  slack_webhook?: string;
}

// ---------------------------------------------------------------------------
// Error class
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function apiFetch<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new ApiError(response.status, `API error: ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(response.status, `API error: ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

async function apiDelete<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { method: "DELETE" });
  if (!response.ok) {
    throw new ApiError(response.status, `API error: ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Price endpoints
// ---------------------------------------------------------------------------

export async function fetchPrices(filters: PriceFilters): Promise<PricePoint[]> {
  const params = new URLSearchParams({
    region: filters.region,
    fuel_type: filters.fuelType,
    months: String(filters.months),
  });
  return apiFetch<PricePoint[]>(`/api/data/prices?${params}`);
}

export async function fetchRegions(): Promise<string[]> {
  return apiFetch<string[]>("/api/data/regions");
}

// ---------------------------------------------------------------------------
// Alert endpoints
// ---------------------------------------------------------------------------

export async function fetchAlerts(): Promise<AlertItem[]> {
  return apiFetch<AlertItem[]>("/api/anomalies");
}

export async function fetchAlertConfigs(): Promise<AlertConfigItem[]> {
  return apiFetch<AlertConfigItem[]>("/api/alert-configs");
}

export async function createAlertConfig(body: AlertConfigCreate): Promise<AlertConfigItem> {
  return apiPost<AlertConfigItem>("/api/alert-configs", body);
}

export async function deleteAlertConfig(id: string): Promise<{ deleted: boolean }> {
  return apiDelete<{ deleted: boolean }>(`/api/alert-configs/${id}`);
}

// ---------------------------------------------------------------------------
// AI Summary endpoints
// ---------------------------------------------------------------------------

export interface MarketSummary {
  region: string;
  summary_text: string;
  generated_at: string;
  data_changed: boolean;
}

export async function fetchSummary(region: string): Promise<MarketSummary | null> {
  try {
    return await apiFetch<MarketSummary>(`/api/summary/${region}`);
  } catch {
    return null; // Silent fail as requested
  }

}

// ---------------------------------------------------------------------------
// Latest Prices endpoint (Data Table)
// ---------------------------------------------------------------------------

export interface LatestPriceRow {
  timestamp: string;
  region: string;
  fuel_type: string;
  price: number;
  unit: string;
  source: string;
  source_url: string;
}

export async function fetchLatestPrices(
  region: string,
  limit: number = 100
): Promise<LatestPriceRow[]> {
  const params = new URLSearchParams({
    region,
    limit: String(limit),
  });
  return apiFetch<LatestPriceRow[]>(`/api/data/prices/latest?${params}`);
}

