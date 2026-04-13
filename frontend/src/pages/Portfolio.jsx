import { useEffect, useState } from 'react';
import { Plus } from 'lucide-react';
import { api, USING_MOCK } from '../lib/api.js';
import { mockPortfolio, mockPositions, mockWatchlist } from '../lib/mockData.js';
import { fmtCurrency, fmtPct, fmtDate, fmtRelativeDays } from '../lib/format.js';
import { cn, pnlClass, verdictStyle } from '../lib/utils.js';
import PortfolioComposition from '../components/PortfolioComposition.jsx';
import PositionDetail from '../components/PositionDetail.jsx';

const TABS = [
  { key: 'holdings', label: 'Holdings' },
  { key: 'watchlist', label: 'Watchlist' },
  { key: 'earnings', label: 'Earnings calendar' }
];

export default function Portfolio() {
  const [tab, setTab] = useState('holdings');
  const [portfolio, setPortfolio] = useState(USING_MOCK ? mockPortfolio : null);
  const [positions, setPositions] = useState(USING_MOCK ? mockPositions : []);
  const [watchlist, setWatchlist] = useState(USING_MOCK ? mockWatchlist : []);

  useEffect(() => {
    if (USING_MOCK) return;
    (async () => {
      try {
        const [p, pos, wl] = await Promise.all([
          api.getPortfolio(),
          api.getPositions(),
          api.getWatchlist()
        ]);
        setPortfolio(p);
        setPositions(pos);
        setWatchlist(wl);
      } catch (e) {
        console.error(e);
      }
    })();
  }, []);

  if (!portfolio) return <div className="text-fg-secondary">Loading…</div>;

  const totalValue = positions.reduce((s, p) => s + p.value, 0);
  const totalInvested = positions.reduce((s, p) => s + p.shares * p.buy_price, 0);
  const totalPnl = totalValue - totalInvested;
  const totalPnlPct = totalInvested ? (totalPnl / totalInvested) * 100 : 0;

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-[22px] font-medium text-fg-primary leading-tight">Portfolio</h1>
          <div className="text-[12px] text-fg-secondary mt-1">
            {positions.length} positions · Invested{' '}
            <span className="font-mono">{fmtCurrency(totalInvested)}</span> · Value{' '}
            <span className="font-mono">{fmtCurrency(totalValue)}</span> ·{' '}
            <span className={cn('font-mono font-medium', pnlClass(totalPnl))}>
              {totalPnl >= 0 ? '+' : ''}
              {fmtCurrency(totalPnl)} ({fmtPct(totalPnlPct, { signed: true })})
            </span>
          </div>
        </div>
        <button className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-accent/15 text-accent border border-accent/30 hover:bg-accent/20 transition-colors text-[12px] font-medium">
          <Plus className="w-4 h-4" />
          Add position
        </button>
      </div>

      <div className="flex gap-1.5">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              'px-4 py-1.5 rounded-md text-[12px] transition-colors',
              tab === t.key
                ? 'bg-accent/15 text-accent font-medium'
                : 'text-fg-secondary hover:text-fg-primary hover:bg-bg-tertiary'
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'holdings' && <HoldingsView positions={positions} portfolio={portfolio} />}
      {tab === 'watchlist' && <WatchlistStub items={watchlist} />}
      {tab === 'earnings' && <EarningsStub positions={positions} />}
    </div>
  );
}

function HoldingsView({ positions, portfolio }) {
  const [selectedTicker, setSelectedTicker] = useState(null);
  const selected = positions.find((p) => p.ticker === selectedTicker);

  const handleSelect = (ticker) => {
    setSelectedTicker((cur) => (cur === ticker ? null : ticker));
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_460px] gap-4">
      <HoldingsTable
        positions={positions}
        selectedTicker={selectedTicker}
        onSelect={handleSelect}
      />
      <div className="bg-bg-secondary rounded-lg p-5">
        {selected ? (
          <PositionDetail position={selected} onClose={() => setSelectedTicker(null)} />
        ) : (
          <PortfolioComposition positions={positions} portfolio={portfolio} />
        )}
      </div>
    </div>
  );
}

function HoldingsTable({ positions, selectedTicker, onSelect }) {
  return (
    <div className="bg-bg-secondary rounded-lg px-4 py-3">
      <div className="grid grid-cols-[70px_80px_68px_76px_58px_68px_84px] gap-2 px-1 py-3 text-[10px] text-fg-tertiary uppercase tracking-wider">
        <div>Ticker</div>
        <div>Purchase</div>
        <div className="text-right">Current</div>
        <div className="text-right">Value</div>
        <div className="text-right">P&amp;L</div>
        <div className="text-right">Stops</div>
        <div className="text-right">AI status</div>
      </div>
      {positions.map((p) => {
        const isSelected = p.ticker === selectedTicker;
        return (
          <button
            key={p.ticker}
            onClick={() => onSelect(p.ticker)}
            className={cn(
              'w-full grid grid-cols-[70px_80px_68px_76px_58px_68px_84px] gap-2 px-1 py-3 items-center border-t border-border/50 text-left transition-colors',
              isSelected ? 'bg-accent/10 border-accent/30' : 'hover:bg-bg-tertiary/30'
            )}
          >
            <div>
              <div
                className={cn(
                  'text-[13px] font-medium',
                  isSelected ? 'text-accent' : 'text-fg-primary'
                )}
              >
                {p.ticker}
              </div>
              <div className="text-[10px] text-fg-tertiary font-mono">{p.shares} sh</div>
            </div>
            <div>
              <div className="text-[11px] text-fg-primary">
                {fmtDate(p.purchase_date, { short: true })}
              </div>
              <div className="text-[10px] text-fg-tertiary font-mono">
                {fmtCurrency(p.buy_price, { decimals: 2 })}
              </div>
            </div>
            <div className="text-right text-[12px] text-fg-primary font-mono">
              {fmtCurrency(p.current_price, { decimals: 2 })}
            </div>
            <div className="text-right text-[12px] font-medium text-fg-primary font-mono">
              {fmtCurrency(p.value)}
            </div>
            <div
              className={cn(
                'text-right text-[12px] font-medium font-mono',
                pnlClass(p.pnl_pct)
              )}
            >
              {fmtPct(p.pnl_pct, { signed: true })}
            </div>
            <div className="text-right text-[10px] text-fg-secondary font-mono leading-tight">
              <div>L {fmtCurrency(p.stop_loss, { decimals: 0 })}</div>
              <div>W {fmtCurrency(p.stop_win, { decimals: 0 })}</div>
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
              <div
                className={cn(
                  'text-[9px] mt-1 font-mono',
                  p.last_verdict.days_ago >= 14 ? 'text-warning' : 'text-fg-tertiary'
                )}
              >
                {fmtRelativeDays(p.last_verdict.days_ago)}
              </div>
            </div>
          </button>
        );
      })}
    </div>
  );
}

function WatchlistStub({ items }) {
  return (
    <div className="bg-bg-secondary rounded-lg px-5 py-6">
      <div className="text-[13px] text-fg-secondary">
        Watchlist table — {items.length} tickers with screener scores, news flags, and promote-to-position action. Implementation pending.
      </div>
    </div>
  );
}

function EarningsStub({ positions }) {
  return (
    <div className="bg-bg-secondary rounded-lg px-5 py-6">
      <div className="text-[13px] text-fg-secondary">
        Timeline of upcoming earnings for {positions.length} held tickers with blackout windows highlighted. Implementation pending.
      </div>
    </div>
  );
}
