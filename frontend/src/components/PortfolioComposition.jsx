import { useMemo } from 'react';
import { fmtCurrency } from '../lib/format.js';
import { cn } from '../lib/utils.js';

// Deterministic sector → color mapping. Unknown sectors get a fallback hsl.
const SECTOR_COLORS = {
  Semiconductors: 'rgb(167 139 250)',
  'Consumer Staples': 'rgb(52 211 153)',
  Financials: 'rgb(96 165 250)',
  'Communication Services': 'rgb(244 114 182)',
  Energy: 'rgb(251 191 36)',
  Hardware: 'rgb(248 113 113)',
  Software: 'rgb(129 140 248)'
};

const sectorColor = (sector, i) =>
  SECTOR_COLORS[sector] || `hsl(${(i * 53) % 360}, 55%, 62%)`;

export default function PortfolioComposition({ positions, portfolio }) {
  const total = portfolio.total_value || positions.reduce((a, p) => a + p.value, 0);

  const sectorData = useMemo(() => {
    const map = new Map();
    positions.forEach((p) => {
      const s = p.sector || 'Other';
      const cur = map.get(s) || { sector: s, value: 0, tickers: [] };
      cur.value += p.value;
      cur.tickers.push(p.ticker);
      map.set(s, cur);
    });
    return Array.from(map.values())
      .map((s) => ({ ...s, pct: (s.value / total) * 100 }))
      .sort((a, b) => b.value - a.value);
  }, [positions, total]);

  const donutSlices = useMemo(() => {
    const sorted = [...positions].sort((a, b) => b.value - a.value);
    const slices = sorted.map((p, i) => ({
      label: p.ticker,
      value: p.value,
      pct: (p.value / total) * 100,
      color: sectorColor(p.sector, i)
    }));
    if (portfolio.cash > 0) {
      slices.push({
        label: 'Cash',
        value: portfolio.cash,
        pct: (portfolio.cash / total) * 100,
        color: 'rgb(91 100 120)'
      });
    }
    return slices;
  }, [positions, portfolio, total]);

  const cashPct = (portfolio.cash / total) * 100;

  return (
    <div className="space-y-5">
      <div>
        <div className="text-[16px] font-medium text-fg-primary">Portfolio composition</div>
        <div className="text-[11px] text-fg-tertiary mt-0.5">
          Select a position to see its detail
        </div>
      </div>

      <div className="flex items-center gap-5">
        <Donut slices={donutSlices} size={168} thickness={22} />
        <div className="flex-1 min-w-0 space-y-3">
          <div>
            <div className="text-[9px] text-fg-tertiary uppercase tracking-wider">Total value</div>
            <div className="text-[20px] font-medium text-fg-primary font-mono mt-1 leading-none">
              {fmtCurrency(total)}
            </div>
          </div>
          <div>
            <div className="text-[9px] text-fg-tertiary uppercase tracking-wider">Cash</div>
            <div className="text-[13px] font-medium text-fg-primary font-mono mt-1">
              {fmtCurrency(portfolio.cash)}
              <span className="text-fg-tertiary ml-2 font-normal">({cashPct.toFixed(1)}%)</span>
            </div>
          </div>
          <div>
            <div className="text-[9px] text-fg-tertiary uppercase tracking-wider">Positions</div>
            <div className="text-[13px] font-medium text-fg-primary font-mono mt-1">
              {positions.length}
            </div>
          </div>
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-3">
          <div className="text-[11px] text-fg-secondary font-medium">Sector allocation</div>
          <div className="text-[10px] text-fg-tertiary">Cap 25%</div>
        </div>
        <div className="space-y-2.5">
          {sectorData.map((s, i) => {
            const cappedPct = Math.min(100, (s.pct / 25) * 100);
            const isOverCap = s.pct > 25;
            const isNearCap = s.pct >= 20 && !isOverCap;
            return (
              <div key={s.sector}>
                <div className="flex justify-between items-baseline text-[11px] mb-1 gap-2">
                  <span className="text-fg-secondary truncate">
                    {s.sector}
                    <span className="text-fg-tertiary ml-2">· {s.tickers.length}</span>
                  </span>
                  <span
                    className={cn(
                      'font-mono font-medium shrink-0',
                      isOverCap ? 'text-danger' : isNearCap ? 'text-warning' : 'text-fg-primary'
                    )}
                  >
                    {s.pct.toFixed(1)}%
                  </span>
                </div>
                <div className="relative h-1.5 bg-bg-primary rounded-full overflow-hidden">
                  <div
                    className={cn(
                      'h-full rounded-full transition-all',
                      isOverCap ? 'bg-danger' : isNearCap ? 'bg-warning' : 'bg-accent'
                    )}
                    style={{ width: `${cappedPct}%`, backgroundColor: !isOverCap && !isNearCap ? sectorColor(s.sector, i) : undefined }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Donut({ slices, size = 168, thickness = 22 }) {
  const cx = size / 2;
  const cy = size / 2;
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;

  let offset = 0;

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0">
      <circle
        cx={cx}
        cy={cy}
        r={radius}
        fill="none"
        stroke="rgb(var(--bg-primary))"
        strokeWidth={thickness}
      />
      {slices.map((s, i) => {
        const length = (s.pct / 100) * circumference;
        const dashArray = `${length} ${circumference - length}`;
        const dashOffset = -offset;
        offset += length;
        return (
          <circle
            key={i}
            cx={cx}
            cy={cy}
            r={radius}
            fill="none"
            stroke={s.color}
            strokeWidth={thickness}
            strokeDasharray={dashArray}
            strokeDashoffset={dashOffset}
            strokeLinecap="butt"
            transform={`rotate(-90 ${cx} ${cy})`}
          />
        );
      })}
    </svg>
  );
}
