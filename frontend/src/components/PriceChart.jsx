import { useMemo, useState } from 'react';
import { fmtCurrency, fmtDate } from '../lib/format.js';
import { cn } from '../lib/utils.js';

const PERIODS = [
  { key: '1W', label: '1W', days: 7 },
  { key: '30D', label: '30D', days: 30 },
  { key: '90D', label: '90D', days: 90 },
  { key: 'YTD', label: 'YTD', days: null }
];

export default function PriceChart({ data, position }) {
  const [period, setPeriod] = useState('90D');
  const [hover, setHover] = useState(null);

  const filtered = useMemo(() => {
    if (!data || !data.length) return [];
    const conf = PERIODS.find((p) => p.key === period);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    let cutoff;
    if (conf.days) {
      cutoff = new Date(today);
      cutoff.setDate(cutoff.getDate() - conf.days);
    } else {
      cutoff = new Date(today.getFullYear(), 0, 1);
    }
    return data.filter((d) => new Date(d.date) >= cutoff);
  }, [data, period]);

  const width = 600;
  const height = 240;
  const padL = 52;
  const padR = 60;
  const padT = 18;
  const padB = 26;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;

  const geo = useMemo(() => {
    if (!filtered.length) return null;
    const prices = filtered.map((d) => d.price);
    const refs = [position.buy_price, position.stop_loss, position.stop_win].filter((v) => v != null);
    const allY = [...prices, ...refs];
    const minRaw = Math.min(...allY);
    const maxRaw = Math.max(...allY);
    const pad = (maxRaw - minRaw) * 0.08 || maxRaw * 0.02;
    const minY = minRaw - pad;
    const maxY = maxRaw + pad;

    const xScale = (i) =>
      filtered.length === 1 ? padL + innerW / 2 : padL + (i / (filtered.length - 1)) * innerW;
    const yScale = (v) => padT + ((maxY - v) / (maxY - minY)) * innerH;

    const path = filtered
      .map((d, i) => `${i === 0 ? 'M' : 'L'} ${xScale(i).toFixed(1)} ${yScale(d.price).toFixed(1)}`)
      .join(' ');

    const yTicks = [0, 0.5, 1].map((t) => {
      const v = minY + (maxY - minY) * (1 - t);
      return { y: padT + t * innerH, label: fmtCurrency(v, { decimals: 0 }) };
    });

    const xTicks = [0, 0.5, 1].map((t) => {
      const idx = Math.round(t * (filtered.length - 1));
      return { x: xScale(idx), label: fmtDate(filtered[idx].date, { short: true }) };
    });

    return { xScale, yScale, minY, maxY, path, yTicks, xTicks };
  }, [filtered, position, innerW, innerH]);

  if (!filtered.length || !geo) {
    return <div className="text-fg-tertiary text-xs py-8 text-center">No data for period</div>;
  }

  const { xScale, yScale, minY, maxY, path, yTicks, xTicks } = geo;
  const latest = filtered[filtered.length - 1];

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="flex gap-1">
          {PERIODS.map((p) => (
            <button
              key={p.key}
              onClick={() => setPeriod(p.key)}
              className={cn(
                'px-2.5 py-1 rounded text-[10px] font-medium transition-colors',
                period === p.key
                  ? 'bg-accent/15 text-accent'
                  : 'text-fg-tertiary hover:text-fg-primary hover:bg-bg-tertiary'
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
        <div className="text-[10px] text-fg-tertiary uppercase tracking-wider">Price</div>
      </div>

      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-[220px] overflow-visible"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const x = ((e.clientX - rect.left) / rect.width) * width;
          const rel = Math.max(0, Math.min(1, (x - padL) / innerW));
          const idx = Math.round(rel * (filtered.length - 1));
          setHover({ idx, x: xScale(idx), d: filtered[idx] });
        }}
      >
        {yTicks.map((t, i) => (
          <g key={i}>
            <line
              x1={padL}
              x2={width - padR}
              y1={t.y}
              y2={t.y}
              stroke="rgb(var(--border-default) / 0.12)"
              strokeDasharray="2 3"
            />
            <text
              x={padL - 8}
              y={t.y + 3}
              fontSize="10"
              textAnchor="end"
              fill="rgb(var(--fg-tertiary))"
              fontFamily="JetBrains Mono, monospace"
            >
              {t.label}
            </text>
          </g>
        ))}

        {xTicks.map((t, i) => (
          <text
            key={i}
            x={t.x}
            y={height - 6}
            fontSize="10"
            textAnchor="middle"
            fill="rgb(var(--fg-tertiary))"
          >
            {t.label}
          </text>
        ))}

        {/* Reference: Buy price (violet) */}
        {position.buy_price >= minY && position.buy_price <= maxY && (
          <g>
            <line
              x1={padL}
              x2={width - padR}
              y1={yScale(position.buy_price)}
              y2={yScale(position.buy_price)}
              stroke="rgb(var(--accent))"
              strokeWidth="1"
              strokeDasharray="4 3"
              opacity="0.75"
            />
            <text
              x={width - padR + 6}
              y={yScale(position.buy_price) + 3}
              fontSize="9"
              fill="rgb(var(--accent))"
              fontFamily="JetBrains Mono, monospace"
            >
              BUY {fmtCurrency(position.buy_price, { decimals: 0 })}
            </text>
          </g>
        )}

        {/* Reference: Stop loss (red) */}
        {position.stop_loss != null && position.stop_loss >= minY && position.stop_loss <= maxY && (
          <g>
            <line
              x1={padL}
              x2={width - padR}
              y1={yScale(position.stop_loss)}
              y2={yScale(position.stop_loss)}
              stroke="rgb(var(--danger))"
              strokeWidth="1"
              strokeDasharray="4 3"
              opacity="0.75"
            />
            <text
              x={width - padR + 6}
              y={yScale(position.stop_loss) + 3}
              fontSize="9"
              fill="rgb(var(--danger))"
              fontFamily="JetBrains Mono, monospace"
            >
              SL {fmtCurrency(position.stop_loss, { decimals: 0 })}
            </text>
          </g>
        )}

        {/* Reference: Stop win (green) */}
        {position.stop_win != null && position.stop_win >= minY && position.stop_win <= maxY && (
          <g>
            <line
              x1={padL}
              x2={width - padR}
              y1={yScale(position.stop_win)}
              y2={yScale(position.stop_win)}
              stroke="rgb(var(--success))"
              strokeWidth="1"
              strokeDasharray="4 3"
              opacity="0.75"
            />
            <text
              x={width - padR + 6}
              y={yScale(position.stop_win) + 3}
              fontSize="9"
              fill="rgb(var(--success))"
              fontFamily="JetBrains Mono, monospace"
            >
              SW {fmtCurrency(position.stop_win, { decimals: 0 })}
            </text>
          </g>
        )}

        <path
          d={path}
          fill="none"
          stroke="rgb(var(--fg-primary))"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        <circle
          cx={xScale(filtered.length - 1)}
          cy={yScale(latest.price)}
          r="3.5"
          fill="rgb(var(--fg-primary))"
        />

        {hover && (
          <g>
            <line
              x1={hover.x}
              x2={hover.x}
              y1={padT}
              y2={height - padB}
              stroke="rgb(var(--border-strong) / 0.4)"
              strokeDasharray="2 2"
            />
            <circle cx={hover.x} cy={yScale(hover.d.price)} r="3" fill="rgb(var(--fg-primary))" />
          </g>
        )}
      </svg>

      {hover && (
        <div className="flex items-center justify-between mt-1 text-[10px] px-1">
          <span className="text-fg-tertiary">{fmtDate(hover.d.date)}</span>
          <span className="font-mono text-fg-primary">
            {fmtCurrency(hover.d.price, { decimals: 2 })}
          </span>
        </div>
      )}
    </div>
  );
}
