import { useMemo, useState } from 'react';
import { fmtPct, fmtDate } from '../lib/format.js';

// Self-contained SVG line chart. No chart library dependency.
export default function PerformanceChart({ data }) {
  const [hover, setHover] = useState(null);

  const { width, height, padL, padR, padT, padB } = {
    width: 800, height: 260, padL: 40, padR: 16, padT: 20, padB: 28
  };
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;

  const { portfolioPath, spyPath, portfolioArea, xScale, yScale, yTicks, xTicks } = useMemo(() => {
    if (!data || data.length === 0) return {};
    const allVals = data.flatMap((d) => [d.portfolio, d.spy]);
    const minV = Math.min(0, ...allVals);
    const maxV = Math.max(...allVals, 1);
    const range = maxV - minV || 1;
    const topV = maxV + range * 0.1;
    const botV = minV - range * 0.05;

    const xScale = (i) => padL + (i / (data.length - 1)) * innerW;
    const yScale = (v) => padT + ((topV - v) / (topV - botV)) * innerH;

    const toPath = (key) =>
      data
        .map((d, i) => {
          const x = xScale(i);
          const y = yScale(d[key]);
          return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
        })
        .join(' ');

    const portfolioPath = toPath('portfolio');
    const spyPath = toPath('spy');

    // Area fill under portfolio line
    const zeroY = yScale(0);
    const portfolioArea =
      portfolioPath +
      ` L ${xScale(data.length - 1).toFixed(1)} ${zeroY.toFixed(1)}` +
      ` L ${xScale(0).toFixed(1)} ${zeroY.toFixed(1)} Z`;

    const yTicks = [0, 0.25, 0.5, 0.75, 1].map((t) => {
      const v = botV + (topV - botV) * (1 - t);
      return { y: padT + t * innerH, label: v.toFixed(0) + '%' };
    });

    const xTicks = [0, 0.33, 0.66, 1].map((t) => {
      const idx = Math.round(t * (data.length - 1));
      return { x: xScale(idx), label: fmtDate(data[idx].date, { short: true }) };
    });

    return { portfolioPath, spyPath, portfolioArea, xScale, yScale, yTicks, xTicks };
  }, [data, innerW, innerH]);

  if (!data || data.length === 0) return null;

  const latest = data[data.length - 1];

  return (
    <div className="w-full">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="text-[16px] font-medium text-fg-primary">Portfolio vs S&amp;P 500</div>
        <div className="flex items-center gap-5 text-[11px]">
          <span className="flex items-center gap-2 text-fg-secondary">
            <span className="w-3 h-[2px] bg-accent" />
            <span>Portfolio</span>
            <span className="text-success font-mono font-medium">
              {fmtPct(latest.portfolio, { signed: true, decimals: 1 })}
            </span>
          </span>
          <span className="flex items-center gap-2 text-fg-secondary">
            <span className="w-3 h-[2px] bg-fg-tertiary" />
            <span>SPY</span>
            <span className="text-fg-primary font-mono font-medium">
              {fmtPct(latest.spy, { signed: true, decimals: 1 })}
            </span>
          </span>
        </div>
      </div>

      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-[240px] overflow-visible"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const x = ((e.clientX - rect.left) / rect.width) * width;
          const rel = Math.max(0, Math.min(1, (x - padL) / innerW));
          const idx = Math.round(rel * (data.length - 1));
          setHover({ idx, x: xScale(idx), d: data[idx] });
        }}
      >
        <defs>
          <linearGradient id="portfolioGrad" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="rgb(var(--accent))" stopOpacity="0.18" />
            <stop offset="100%" stopColor="rgb(var(--accent))" stopOpacity="0" />
          </linearGradient>
        </defs>

        {yTicks.map((t, i) => (
          <g key={i}>
            <line
              x1={padL}
              x2={width - padR}
              y1={t.y}
              y2={t.y}
              stroke="rgb(var(--border-default) / 0.15)"
              strokeDasharray="2 3"
            />
            <text x={padL - 8} y={t.y + 3} fontSize="10" textAnchor="end" fill="rgb(var(--fg-tertiary))">
              {t.label}
            </text>
          </g>
        ))}

        {xTicks.map((t, i) => (
          <text
            key={i}
            x={t.x}
            y={height - 8}
            fontSize="10"
            textAnchor="middle"
            fill="rgb(var(--fg-tertiary))"
          >
            {t.label}
          </text>
        ))}

        <path d={portfolioArea} fill="url(#portfolioGrad)" />

        <path
          d={spyPath}
          fill="none"
          stroke="rgb(var(--fg-tertiary))"
          strokeWidth="1.5"
          strokeDasharray="4 3"
          opacity="0.7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        <path
          d={portfolioPath}
          fill="none"
          stroke="rgb(var(--accent))"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        <circle
          cx={xScale(data.length - 1)}
          cy={yScale(data[data.length - 1].portfolio)}
          r="4"
          fill="rgb(var(--accent))"
        />
        <circle
          cx={xScale(data.length - 1)}
          cy={yScale(data[data.length - 1].portfolio)}
          r="8"
          fill="rgb(var(--accent))"
          opacity="0.2"
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
            <circle cx={hover.x} cy={yScale(hover.d.portfolio)} r="3.5" fill="rgb(var(--accent))" />
            <circle cx={hover.x} cy={yScale(hover.d.spy)} r="3" fill="rgb(var(--fg-tertiary))" />
          </g>
        )}
      </svg>

      {hover && (
        <div className="flex items-center justify-between mt-2 text-[11px] px-1">
          <span className="text-fg-tertiary">{fmtDate(hover.d.date)}</span>
          <span className="flex gap-4">
            <span className="font-mono text-accent">{fmtPct(hover.d.portfolio, { signed: true, decimals: 2 })}</span>
            <span className="font-mono text-fg-secondary">{fmtPct(hover.d.spy, { signed: true, decimals: 2 })}</span>
          </span>
        </div>
      )}
    </div>
  );
}
