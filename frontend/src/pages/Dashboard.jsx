import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import { api, USING_MOCK } from '../lib/api.js';
import {
  mockPortfolio,
  mockPositions,
  mockWatchlist,
  mockRecentDebates,
  mockPerformanceSeries
} from '../lib/mockData.js';
import { fmtCurrency, fmtPct, fmtDate, fmtTime, fmtRelativeDays } from '../lib/format.js';
import { cn, pnlClass, verdictStyle, phaseStyle } from '../lib/utils.js';
import KpiCard from '../components/KpiCard.jsx';
import RegimeBadge from '../components/RegimeBadge.jsx';
import PerformanceChart from '../components/PerformanceChart.jsx';

const WIN_RATE_TABS = [
  { key: '1d', label: '1D' },
  { key: '1w', label: '1W' },
  { key: '1m', label: '1M' },
  { key: 'all', label: 'ALL' }
];

export default function Dashboard() {
  const [portfolio, setPortfolio] = useState(USING_MOCK ? mockPortfolio : null);
  const [positions, setPositions] = useState(USING_MOCK ? mockPositions : []);
  const [watchlist, setWatchlist] = useState(USING_MOCK ? mockWatchlist : []);
  const [debates, setDebates] = useState(USING_MOCK ? mockRecentDebates : []);
  const [series] = useState(mockPerformanceSeries);
  const [winRateKey, setWinRateKey] = useState('1w');

  useEffect(() => {
    if (USING_MOCK) return;
    let alive = true;
    (async () => {
      try {
        const [p, pos, wl, deb] = await Promise.all([
          api.getPortfolio(),
          api.getPositions(),
          api.getWatchlist(),
          api.getRecentDebates(5)
        ]);
        if (!alive) return;
        setPortfolio(p);
        setPositions(pos);
        setWatchlist(wl);
        setDebates(deb);
      } catch (e) {
        console.error('Failed to load dashboard data:', e);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (!portfolio) return <div className="text-fg-secondary">Loading…</div>;

  const topPositions = [...positions].sort((a, b) => b.value - a.value).slice(0, 5);
  const topWatchlist = watchlist.slice(0, 4);
  const winRate = portfolio.win_rate?.[winRateKey];

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-[22px] font-medium text-fg-primary leading-tight">Dashboard</h1>
          <div className="text-[12px] text-fg-secondary mt-1">
            Last update {fmtTime(portfolio.updated_at)} ET · {fmtDate(portfolio.updated_at)}
          </div>
        </div>
        <RegimeBadge label={portfolio.regime?.label} vix={portfolio.regime?.vix} />
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-6 gap-2.5">
        <KpiCard label="Total portfolio" value={fmtCurrency(portfolio.total_value)} />
        <KpiCard label="Invested" value={fmtCurrency(portfolio.invested)} />
        <KpiCard label="Cash" value={fmtCurrency(portfolio.cash)} />
        <KpiCard
          label="% G/L total"
          value={fmtPct(portfolio.gain_loss_total_pct, { signed: true })}
          valueClass={pnlClass(portfolio.gain_loss_total_pct)}
        />
        <KpiCard
          label="% G/L invested"
          value={fmtPct(portfolio.gain_loss_invested_pct, { signed: true })}
          valueClass={pnlClass(portfolio.gain_loss_invested_pct)}
        />
        <div className="bg-bg-secondary rounded-md px-4 py-3 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <div className="text-[10px] text-fg-tertiary uppercase tracking-wider">Win rate</div>
            <div className="flex gap-0.5">
              {WIN_RATE_TABS.map((t) => (
                <button
                  key={t.key}
                  onClick={() => setWinRateKey(t.key)}
                  className={cn(
                    'text-[9px] px-1.5 py-0.5 rounded transition-colors',
                    winRateKey === t.key
                      ? 'bg-accent/20 text-accent'
                      : 'text-fg-tertiary hover:text-fg-secondary'
                  )}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
          <div className="text-[22px] font-medium text-fg-primary leading-none mt-2 font-mono">
            {winRate != null ? `${(winRate * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
      </div>

      {/* Row 2: Chart (2fr) + Watchlist (1fr) */}
      <div className="grid grid-cols-3 gap-4">
        <div className="col-span-2 bg-bg-secondary rounded-lg p-5">
          <PerformanceChart data={series} />
        </div>

        <div className="bg-bg-secondary rounded-lg p-5 flex flex-col">
          <div className="flex items-center justify-between mb-4">
            <div className="text-[16px] font-medium text-fg-primary">Watchlist</div>
            <div className="text-[10px] text-fg-tertiary uppercase tracking-wider">
              {watchlist.length} tickers
            </div>
          </div>
          <div className="space-y-3.5 flex-1">
            {topWatchlist.map((w) => (
              <div key={w.ticker} className="flex items-center justify-between">
                <div className="min-w-0">
                  <div className="text-[13px] font-medium text-fg-primary flex items-center gap-2">
                    {w.ticker}
                    {w.news_flag && <span className="w-1.5 h-1.5 rounded-full bg-warning" title="News flag" />}
                  </div>
                  <div className="text-[10px] text-fg-tertiary">
                    {w.score != null ? `Score ${w.score.toFixed(1)}` : w.sector}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-[12px] font-medium text-fg-primary font-mono">
                    {fmtCurrency(w.price, { decimals: 2 })}
                  </div>
                  <div className={cn('text-[10px] font-mono', pnlClass(w.change_1d_pct))}>
                    {fmtPct(w.change_1d_pct, { signed: true })}
                  </div>
                </div>
              </div>
            ))}
          </div>
          <Link
            to="/portfolio"
            className="mt-4 text-[11px] text-accent hover:text-accent/80 flex items-center justify-end gap-1 transition-colors"
          >
            View all <ChevronRight className="w-3 h-3" />
          </Link>
        </div>
      </div>

      {/* Row 3: Positions (3fr) + Recent debates (2fr) */}
      <div className="grid grid-cols-5 gap-4">
        <div className="col-span-3 bg-bg-secondary rounded-lg p-5">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[16px] font-medium text-fg-primary">My positions</div>
            <Link
              to="/portfolio"
              className="text-[11px] text-accent hover:text-accent/80 flex items-center gap-1 transition-colors"
            >
              View all {positions.length} <ChevronRight className="w-3 h-3" />
            </Link>
          </div>

          <div className="grid grid-cols-[72px_minmax(0,1fr)_88px_64px_72px] gap-3 py-2 px-1 border-t border-border/50 text-[10px] text-fg-tertiary uppercase tracking-wider">
            <div>Ticker</div>
            <div>Name · Shares</div>
            <div className="text-right">Value</div>
            <div className="text-right">P&amp;L</div>
            <div className="text-right">Verdict</div>
          </div>

          {topPositions.map((p) => (
            <Link
              key={p.ticker}
              to="/portfolio"
              className="grid grid-cols-[72px_minmax(0,1fr)_88px_64px_72px] gap-3 items-center py-2.5 px-1 border-t border-border/50 hover:bg-bg-tertiary/50 transition-colors rounded"
            >
              <div className="text-[13px] font-medium text-fg-primary">{p.ticker}</div>
              <div className="text-[11px] text-fg-secondary truncate">
                {p.name} · {p.shares} sh @ {fmtCurrency(p.buy_price, { decimals: 0 })}
              </div>
              <div className="text-right">
                <div className="text-[12px] font-medium text-fg-primary font-mono">
                  {fmtCurrency(p.value)}
                </div>
                <div className="text-[9px] text-fg-tertiary font-mono">
                  {p.allocation_pct.toFixed(1)}% alloc
                </div>
              </div>
              <div className={cn('text-right text-[12px] font-medium font-mono', pnlClass(p.pnl_pct))}>
                {fmtPct(p.pnl_pct, { signed: true })}
              </div>
              <div className="text-right">
                <span
                  className={cn(
                    'inline-block px-2 py-0.5 rounded-full text-[10px] font-medium tracking-wide',
                    verdictStyle(p.last_verdict.action)
                  )}
                >
                  {p.last_verdict.action}
                  {p.last_verdict.confidence != null && ` ${p.last_verdict.confidence}`}
                </span>
              </div>
            </Link>
          ))}
        </div>

        <div className="col-span-2 bg-bg-secondary rounded-lg p-5 flex flex-col">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[16px] font-medium text-fg-primary">Recent AI debates</div>
            <Link
              to="/insights"
              className="text-[11px] text-accent hover:text-accent/80 flex items-center gap-1 transition-colors"
            >
              View all <ChevronRight className="w-3 h-3" />
            </Link>
          </div>
          <div className="flex-1 flex flex-col">
            {debates.slice(0, 5).map((d) => (
              <Link
                key={d.id}
                to={`/insights/${d.id}`}
                className="flex items-center justify-between gap-3 py-2.5 px-1 border-t border-border/50 hover:bg-bg-tertiary/50 transition-colors rounded"
              >
                <div className="min-w-0">
                  <div className="text-[13px] font-medium text-fg-primary">{d.ticker}</div>
                  <div className="text-[10px] text-fg-tertiary mt-0.5">
                    <span className={cn('px-1.5 py-0.5 rounded', phaseStyle(d.phase))}>{d.phase}</span>
                    <span className="ml-2 font-mono">{fmtTime(d.timestamp)}</span>
                  </div>
                </div>
                <span
                  className={cn(
                    'px-2 py-0.5 rounded-full text-[10px] font-medium tracking-wide',
                    verdictStyle(d.verdict.action)
                  )}
                >
                  {d.verdict.action} {d.verdict.confidence}
                </span>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
