import { useEffect, useState } from "react";
import { Sparkles, Clock } from "lucide-react";

import { fetchSummary } from "../api/api";
import type { MarketSummary } from "../api/api";

interface MarketSummaryProps {
  region: string;
}

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

export default function MarketSummaryCard({ region }: MarketSummaryProps) {
  const [summary, setSummary] = useState<MarketSummary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!region) return;
    
    setLoading(true);
    let isSubscribed = true;

    fetchSummary(region).then((data) => {
      if (isSubscribed) {
        setSummary(data);
        setLoading(false);
      }
    });

    return () => {
      isSubscribed = false;
    };
  }, [region]);

  // If we finished loading and got null (error or no data), fail silently
  if (!loading && !summary) {
    return null;
  }

  // Loading state (shimmer)
  if (loading) {
    return (
      <div className="bg-bg-card border border-border border-l-[3px] border-l-border
                      rounded-xl p-5 mb-6 animate-shimmer h-[104px]">
      </div>
    );
  }

  // Loaded state
  return (
    <div className="bg-bg-card border border-border border-l-[3px] border-l-accent
                    shadow-[0_0_20px_rgba(59,130,246,0.05)] rounded-xl p-5 mb-6
                    animate-fade-in relative overflow-hidden group">
                    
      {/* Background glow orb top right */}
      <div className="absolute -top-12 -right-12 w-32 h-32 bg-accent/5 rounded-full blur-2xl 
                      transition-opacity duration-700 group-hover:bg-accent/10" />

      <div className="relative z-10">
        <div className="flex items-start justify-between gap-4 mb-3">
          <p className="text-[14.5px] leading-relaxed text-text-secondary pr-8">
            {summary!.summary_text}
          </p>
          
          {/* Sparkle badge */}
          <div className="flex items-center justify-center w-8 h-8 rounded-lg 
                          bg-accent/10 text-accent shrink-0">
            <Sparkles className="w-4 h-4" />
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 text-text-muted">
            <Clock className="w-3.5 h-3.5" />
            <span className="text-[11px] font-mono tracking-wide">
              GENERATED {timeAgo(summary!.generated_at).toUpperCase()}
            </span>
          </div>
          
          {/* Data freshness indicator */}
          <div className="flex items-center gap-1.5">
            <span className="text-text-muted text-[10px]">|</span>
            {summary!.data_changed ? (
              <span className="flex items-center gap-1 text-[10px] font-mono font-bold tracking-wide text-success">
                <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse-dot" />
                LIVE DATA
              </span>
            ) : (
              <span className="flex items-center gap-1 text-[10px] font-mono font-bold tracking-wide text-warning">
                <span className="w-1.5 h-1.5 rounded-full bg-warning" />
                STALE DATA
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
