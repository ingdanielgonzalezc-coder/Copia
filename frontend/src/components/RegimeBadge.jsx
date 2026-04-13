import { cn } from '../lib/utils.js';
import { regimeStyle } from '../lib/utils.js';

const labelMap = {
  BULL_TREND: 'Bull trend',
  NEUTRAL: 'Neutral',
  CORRECTION: 'Correction',
  HIGH_VOLATILITY: 'High volatility'
};

export default function RegimeBadge({ label, vix }) {
  return (
    <div
      className={cn(
        'inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-[11px] font-medium tracking-wide',
        regimeStyle(label)
      )}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
      <span className="uppercase">{labelMap[label] || label}</span>
      {vix != null && <span className="opacity-70 font-mono">· VIX {vix.toFixed(1)}</span>}
    </div>
  );
}
