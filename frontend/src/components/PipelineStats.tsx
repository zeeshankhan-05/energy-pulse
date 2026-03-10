import { useEffect, useState } from "react";
import { Info } from "lucide-react";
import { fetchPipelineStats } from "../api/api";
import type { PipelineStatsResponse } from "../api/api";

export default function PipelineStats() {
  const [data, setData] = useState<PipelineStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isSubscribed = true;
    setLoading(true);
    fetchPipelineStats()
      .then((res) => {
        if (isSubscribed) {
          setData(res);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (isSubscribed) {
          setError(err instanceof Error ? err.message : "Failed to load pipeline stats");
          setLoading(false);
        }
      });
    return () => {
      isSubscribed = false;
    };
  }, []);

  const totalInserted =
    data?.daily_stats.reduce(
      (acc, day) =>
        acc + day.sources.reduce((sAcc, src) => sAcc + src.records_inserted, 0),
      0
    ) || 0;

  if (loading) {
    return (
      <div className="mt-10 space-y-4">
        <h3 className="text-lg font-bold tracking-wide text-text-primary">
          Data Pipeline Activity
        </h3>
        <div className="bg-bg-card border border-border rounded-xl p-8 animate-pulse flex flex-col gap-4">
          <div className="h-4 w-64 bg-gray-700/50 rounded" />
          <div className="h-6 w-full bg-gray-700/50 rounded" />
          <div className="h-6 w-full bg-gray-700/50 rounded" />
        </div>
      </div>
    );
  }

  if (error || !data) {
    return null;
  }

  return (
    <div className="mt-10 space-y-4">
      <div>
        <h3 className="text-lg font-bold tracking-wide text-text-primary">
          Data Pipeline Activity
        </h3>
        <p className="text-sm text-text-muted mt-1">
          Showing the last 7 days of automated data ingestion runs
        </p>
      </div>

      <div className="bg-accent/10 border border-accent/20 rounded-xl p-4 text-sm text-accent">
        <b>Summary:</b> {totalInserted} electricity and natural gas prices were ingested in the last 7 days — these represent monthly residential price snapshots from sources like the EIA and state PUCs, maintaining an accurate and up-to-date dataset.
      </div>

      <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead className="bg-bg-primary/50 text-xs uppercase tracking-wider text-text-muted border-b border-border">
              <tr>
                <th className="py-3 px-4 font-semibold whitespace-nowrap">Date</th>
                <th className="py-3 px-4 font-semibold whitespace-nowrap">Source</th>
                <th className="py-3 px-4 font-semibold whitespace-nowrap group relative cursor-help">
                  <div className="flex items-center gap-1.5" title="Inserted means new price records added to the database">
                    Inserted <Info className="w-3.5 h-3.5" />
                  </div>
                </th>
                <th className="py-3 px-4 font-semibold whitespace-nowrap group relative cursor-help">
                  <div className="flex items-center gap-1.5" title="Rejected means records that failed validation">
                    Rejected <Info className="w-3.5 h-3.5" />
                  </div>
                </th>
                <th className="py-3 px-4 font-semibold whitespace-nowrap group relative cursor-help">
                  <div className="flex items-center gap-1.5" title="Duplicates means records that already existed and were skipped">
                    Duplicates <Info className="w-3.5 h-3.5" />
                  </div>
                </th>
              </tr>
            </thead>
            <tbody className="text-sm divide-y divide-white/5">
              {data.daily_stats.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-text-muted">
                    No pipeline activity recorded in the last 7 days.
                  </td>
                </tr>
              ) : (
                data.daily_stats.flatMap((day) =>
                  day.sources.map((src, idx) => (
                    <tr
                      key={`${day.date}-${src.source}-${idx}`}
                      className="hover:bg-white/5 transition-colors group"
                    >
                      <td className="py-2.5 px-4 text-text-secondary whitespace-nowrap">
                        {idx === 0 ? day.date : ""}
                      </td>
                      <td className="py-2.5 px-4 font-semibold text-text-primary">
                        {src.source}
                      </td>
                      <td className="py-2.5 px-4 font-mono font-bold text-success">
                        {src.records_inserted}
                      </td>
                      <td className="py-2.5 px-4 font-mono font-bold text-critical">
                        {src.records_rejected}
                      </td>
                      <td className="py-2.5 px-4 font-mono text-text-muted">
                        {src.records_duplicates}
                      </td>
                    </tr>
                  ))
                )
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
