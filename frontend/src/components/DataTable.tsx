import { useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Download,
  Search,
} from "lucide-react";

import { fetchLatestPrices } from "../api/api";
import type { LatestPriceRow } from "../api/api";

type SortCol = keyof LatestPriceRow;
type SortDir = "asc" | "desc";
type FuelFilter = "all" | "electricity" | "natural_gas";

interface DataTableProps {
  region: string;
}

const ITEMS_PER_PAGE = 20;

export default function DataTable({ region }: DataTableProps) {
  const [data, setData] = useState<LatestPriceRow[]>([]);
  const [loading, setLoading] = useState(true);

  // Filters & sorting state
  const [sortCol, setSortCol] = useState<SortCol>("timestamp");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [searchTerm, setSearchTerm] = useState("");
  const [fuelFilter, setFuelFilter] = useState<FuelFilter>("all");
  const [currentPage, setCurrentPage] = useState(1);

  // Fetch data when region changes
  useEffect(() => {
    if (!region) return;
    setLoading(true);
    let isSubscribed = true;

    fetchLatestPrices(region, 200).then((res) => {
      if (isSubscribed) {
        setData(res);
        setLoading(false);
        setCurrentPage(1); // reset on new fetch
      }
    });

    return () => {
      isSubscribed = false;
    };
  }, [region]);

  // Reset pagination when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [searchTerm, fuelFilter]);

  // 1. Filter pipeline
  const processedData = useMemo(() => {
    let result = [...data];

    // a) Fuel filter
    if (fuelFilter !== "all") {
      result = result.filter((r) => r.fuel_type === fuelFilter);
    }

    // b) Search term (region or source)
    if (searchTerm.trim()) {
      const lowerSearch = searchTerm.toLowerCase();
      result = result.filter(
        (r) =>
          r.region.toLowerCase().includes(lowerSearch) ||
          r.source.toLowerCase().includes(lowerSearch)
      );
    }

    // c) Sort
    result.sort((a, b) => {
      const aVal = a[sortCol];
      const bVal = b[sortCol];

      let comparison = 0;
      if (sortCol === "price") {
        comparison = (aVal as number) - (bVal as number);
      } else {
        // String comparison fallback
        comparison = String(aVal).localeCompare(String(bVal));
      }

      return sortDir === "asc" ? comparison : -comparison;
    });

    return result;
  }, [data, fuelFilter, searchTerm, sortCol, sortDir]);

  // 2. Pagination calculation
  const totalItems = processedData.length;
  const totalPages = Math.ceil(totalItems / ITEMS_PER_PAGE);
  const startIndex = (currentPage - 1) * ITEMS_PER_PAGE;
  const endIndex = Math.min(startIndex + ITEMS_PER_PAGE, totalItems);
  const currentData = processedData.slice(startIndex, endIndex);

  // Helpers
  const handleSort = (col: SortCol) => {
    if (sortCol === col) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortCol(col);
      setSortDir("desc");
      setCurrentPage(1);
    }
  };

  const exportCSV = () => {
    if (processedData.length === 0) return;

    // Headers
    const headers = [
      "Timestamp",
      "Region",
      "Fuel Type",
      "Price",
      "Unit",
      "Source",
      "Source URL",
    ].join(",");

    // Rows
    const csvRows = processedData.map((row) => {
      return [
        `"${row.timestamp}"`,
        `"${row.region}"`,
        `"${row.fuel_type}"`,
        row.price,
        `"${row.unit}"`,
        `"${row.source}"`,
        `"${row.source_url}"`,
      ].join(",");
    });

    const csvContent = [headers, ...csvRows].join("\n");
    const today = new Date().toISOString().split("T")[0];
    const filename = `energypulse-prices-${region}-${today}.csv`;

    // Trigger download
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.setAttribute("download", filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const formatTimestamp = (iso: string) => {
    const d = new Date(iso);
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(d);
  };

  const SortIcon = ({ column }: { column: SortCol }) => {
    if (sortCol !== column) {
      return (
        <ArrowUpDown className="w-3.5 h-3.5 text-text-muted opacity-0 group-hover:opacity-100 transition-opacity" />
      );
    }
    return sortDir === "asc" ? (
      <ArrowUp className="w-3.5 h-3.5 text-accent" />
    ) : (
      <ArrowDown className="w-3.5 h-3.5 text-accent" />
    );
  };

  // ---------------------------------------------------------------------------
  // Render sub-components
  // ---------------------------------------------------------------------------

  const FilterPill = ({
    label,
    value,
  }: {
    label: string;
    value: FuelFilter;
  }) => {
    const active = fuelFilter === value;
    return (
      <button
        onClick={() => setFuelFilter(value)}
        className={`px-3 py-1.5 rounded-md text-xs font-semibold tracking-wide transition-colors
          ${
            active
              ? "bg-accent text-white"
              : "bg-white/5 text-text-secondary hover:bg-white/10 hover:text-text-primary"
          }
        `}
      >
        {label}
      </button>
    );
  };

  return (
    <div className="mt-10 space-y-4">
      <h3 className="text-lg font-bold tracking-wide text-text-primary">
        Latest Snapshots
      </h3>

      {/* FILTER BAR */}
      <div className="flex flex-wrap items-center justify-between gap-4 bg-bg-card border border-border p-4 rounded-xl">
        <div className="flex flex-wrap items-center gap-4 flex-1">
          {/* Search */}
          <div className="relative w-64">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
            <input
              type="text"
              placeholder="Filter by region or source..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-bg-primary border border-border text-text-primary text-sm 
                         rounded-lg pl-9 pr-3 py-2 outline-none focus:border-accent
                         placeholder:text-text-muted transition-colors"
            />
          </div>

          {/* Fuel Filters */}
          <div className="flex gap-1">
            <FilterPill label="All" value="all" />
            <FilterPill label="Electricity" value="electricity" />
            <FilterPill label="Natural Gas" value="natural_gas" />
          </div>
        </div>

        {/* CSV Export */}
        <button
          onClick={exportCSV}
          disabled={loading || processedData.length === 0}
          className="flex items-center gap-2 bg-white/5 hover:bg-white/10 border border-border 
                     text-text-primary text-sm font-semibold tracking-wide px-4 py-2 rounded-lg
                     transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Download className="w-4 h-4" />
          Export CSV
        </button>
      </div>

      {/* DATA TABLE */}
      <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead className="bg-bg-primary/50 text-xs uppercase tracking-wider text-text-muted sticky top-0 border-b border-border">
              <tr>
                <th
                  className="py-3 px-4 font-semibold cursor-pointer group whitespace-nowrap"
                  onClick={() => handleSort("timestamp")}
                >
                  <div className="flex items-center gap-1.5">
                    Timestamp <SortIcon column="timestamp" />
                  </div>
                </th>
                <th
                  className="py-3 px-4 font-semibold cursor-pointer group whitespace-nowrap"
                  onClick={() => handleSort("region")}
                >
                  <div className="flex items-center gap-1.5">
                    Region <SortIcon column="region" />
                  </div>
                </th>
                <th
                  className="py-3 px-4 font-semibold cursor-pointer group whitespace-nowrap"
                  onClick={() => handleSort("fuel_type")}
                >
                  <div className="flex items-center gap-1.5">
                    Fuel Type <SortIcon column="fuel_type" />
                  </div>
                </th>
                <th
                  className="py-3 px-4 font-semibold cursor-pointer group whitespace-nowrap text-right"
                  onClick={() => handleSort("price")}
                >
                  <div className="flex items-center justify-end gap-1.5">
                    <SortIcon column="price" /> Price
                  </div>
                </th>
                <th className="py-3 px-4 font-semibold whitespace-nowrap">
                  Unit
                </th>
                <th
                  className="py-3 px-4 font-semibold cursor-pointer group whitespace-nowrap"
                  onClick={() => handleSort("source")}
                >
                  <div className="flex items-center gap-1.5">
                    Source <SortIcon column="source" />
                  </div>
                </th>
              </tr>
            </thead>
            <tbody className="text-sm divide-y divide-white/5">
              {loading ? (
                // Skeletons
                Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i} className="animate-pulse">
                    <td className="py-2.5 px-4"><div className="h-4 w-32 bg-gray-700/50 rounded" /></td>
                    <td className="py-2.5 px-4"><div className="h-4 w-8 bg-gray-700/50 rounded" /></td>
                    <td className="py-2.5 px-4"><div className="h-5 w-24 bg-gray-700/50 rounded-full" /></td>
                    <td className="py-2.5 px-4 flex justify-end"><div className="h-4 w-16 bg-gray-700/50 rounded" /></td>
                    <td className="py-2.5 px-4"><div className="h-4 w-12 bg-gray-700/50 rounded" /></td>
                    <td className="py-2.5 px-4"><div className="h-4 w-20 bg-gray-700/50 rounded" /></td>
                  </tr>
                ))
              ) : currentData.length === 0 ? (
                <tr>
                  <td
                    colSpan={6}
                    className="py-8 text-center text-text-muted"
                  >
                    No data found for the current filters.
                  </td>
                </tr>
              ) : (
                currentData.map((row) => (
                  <tr
                    key={`${row.timestamp}-${row.source}-${row.fuel_type}`}
                    className="hover:bg-white/5 transition-colors group"
                  >
                    <td className="py-2.5 px-4 text-text-secondary whitespace-nowrap">
                      {formatTimestamp(row.timestamp)}
                    </td>
                    <td className="py-2.5 px-4 font-semibold text-text-primary">
                      {row.region}
                    </td>
                    <td className="py-2.5 px-4">
                      {row.fuel_type === "electricity" ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-accent/10 text-accent">
                          Electricity
                        </span>
                      ) : (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-warning/10 text-warning">
                          Natural Gas
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 px-4 text-right font-mono font-bold text-text-primary">
                      {row.price !== null ? row.price.toFixed(4) : "—"}
                    </td>
                    <td className="py-2.5 px-4 text-text-muted">
                      {row.unit}
                    </td>
                    <td className="py-2.5 px-4">
                      <a
                        href={row.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-text-secondary underline decoration-white/20 hover:text-accent hover:decoration-accent transition-colors truncate block max-w-[150px]"
                        title={row.source}
                      >
                        {row.source}
                      </a>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* PAGINATION FOOTER */}
        {!loading && totalItems > 0 && (
          <div className="flex items-center justify-between px-4 py-3 bg-bg-primary/30 border-t border-border">
            <div className="text-xs text-text-muted">
              Showing <span className="font-semibold text-text-primary">{startIndex + 1}</span>–
              <span className="font-semibold text-text-primary">{endIndex}</span> of{" "}
              <span className="font-semibold text-text-primary">{totalItems}</span> results
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                className="px-3 py-1.5 rounded bg-bg-primary border border-border text-xs font-semibold
                           text-text-primary hover:bg-white/5 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Prev
              </button>
              <button
                onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                disabled={currentPage === totalPages}
                className="px-3 py-1.5 rounded bg-bg-primary border border-border text-xs font-semibold
                           text-text-primary hover:bg-white/5 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
