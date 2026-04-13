import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Edit, XCircle, Trash2, ExternalLink, AlertTriangle, X } from 'lucide-react';
import { fmtCurrency, fmtPct, fmtDate } from '../lib/format.js';
import { cn, pnlClass, verdictStyle } from '../lib/utils.js';
import { mockPriceHistory } from '../lib/mockData.js';
import PriceChart from './PriceChart.jsx';

export default function PositionDetail({ position, onClose }) {
  const history = useMemo(() => mockPriceHistory(position), [position]);

  const daysHeld = Math.floor(
    (new Date() - new Date(position.purchase_date)) / (1000 * 60 * 60 * 24)
  );
  const totalInvested = position.shares * position.buy_price;
  const pnlDollar = position.value - totalInvested;
  const distToSL = position.stop_loss
    ? ((position.current_price - position.stop_loss) / position.current_price) * 100
    : null;
  const distToSW = position.stop_win
    ? ((position.stop_win - position.current_price) / position.current_price) * 100
    : null;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <div className="text-[20px] font-medium text-fg-primary">{position.ticker}</div>
            <div className="text-[12px] text-fg-secondary truncate">{position.name}</div>
          </div>
          <div className="text-[11px] text-fg-tertiary mt-0.5">{position.sector}</div>
        </div>
        <div className="flex items-start gap-3">
          <div className="text-right">
            <div className="text-[20px] font-medium text-fg-primary font-mono leading-none">
              {fmtCurrency(position.current_price, { decimals: 2 })}
            </div>
            <div className={cn('text-[12px] font-medium font-mono mt-1', pnlClass(position.pnl_pct))}>
              {fmtPct(position.pnl_pct, { signed: true })}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close detail"
            className="w-6 h-6 rounded flex items-center justify-center text-fg-tertiary hover:text-fg-primary hover:bg-bg-tertiary transition-colors -mr-1 -mt-1"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {position.last_verdict?.blackout && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-md bg-warning/10 border border-warning/30">
          <AlertTriangle className="w-4 h-4 text-warning shrink-0 mt-0.5" />
          <div className="text-[11px] text-warning leading-relaxed">
            Earnings blackout active — no actions permitted until after reporting
          </div>
        </div>
      )}

      <div className="border border-border/50 rounded-md p-4">
        <PriceChart data={history} position={position} />
      </div>

      <div className="grid grid-cols-4 gap-2">
        <StatCell label="Shares" value={position.shares} />
        <StatCell label="Days held" value={daysHeld} />
        <StatCell label="Allocation" value={`${position.allocation_pct.toFixed(1)}%`} />
        <StatCell label="Entry" value={fmtCurrency(position.buy_price, { decimals: 0 })} />
        <StatCell label="Value" value={fmtCurrency(position.value)} />
        <StatCell label="Invested" value={fmtCurrency(totalInvested)} />
        <StatCell
          label="P&L $"
          value={`${pnlDollar >= 0 ? '+' : ''}${fmtCurrency(pnlDollar, { decimals: 0 })}`}
          className={pnlClass(pnlDollar)}
        />
        <StatCell
          label="To SL / SW"
          value={
            distToSL != null && distToSW != null
              ? `${distToSL.toFixed(1)}% / ${distToSW.toFixed(1)}%`
              : '—'
          }
          compact
        />
      </div>

      {position.last_verdict && !position.last_verdict.blackout && (
        <div className="border border-border/50 rounded-md p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[10px] text-fg-tertiary uppercase tracking-wide font-medium">
              Last AI verdict
            </div>
            <Link
              to="/insights"
              className="text-[10px] text-accent hover:text-accent/80 flex items-center gap-1 transition-colors"
            >
              View full debate <ExternalLink className="w-3 h-3" />
            </Link>
          </div>
          <div className="flex items-center gap-3 mb-2 flex-wrap">
            <span
              className={cn(
                'px-2.5 py-1 rounded-full text-[11px] font-medium',
                verdictStyle(position.last_verdict.action)
              )}
            >
              {position.last_verdict.action} · conf {position.last_verdict.confidence}
            </span>
            <span className="text-[11px] text-fg-tertiary">
              {position.last_verdict.days_ago === 0
                ? 'today'
                : `${position.last_verdict.days_ago}d ago`}
            </span>
          </div>
          <div className="text-[11px] text-fg-secondary leading-relaxed">
            Position at {fmtPct(position.pnl_pct, { signed: true })} from entry. Stop protected at{' '}
            {fmtCurrency(position.stop_loss, { decimals: 0 })}, target{' '}
            {fmtCurrency(position.stop_win, { decimals: 0 })}.
          </div>
        </div>
      )}

      <div className="flex gap-2 pt-1">
        <button className="flex-1 px-3 py-2 rounded-md bg-bg-tertiary hover:bg-bg-hover border border-border/50 text-[12px] text-fg-primary font-medium flex items-center justify-center gap-2 transition-colors">
          <Edit className="w-3.5 h-3.5" />
          Edit
        </button>
        <button className="flex-1 px-3 py-2 rounded-md bg-bg-tertiary hover:bg-bg-hover border border-border/50 text-[12px] text-fg-primary font-medium flex items-center justify-center gap-2 transition-colors">
          <XCircle className="w-3.5 h-3.5" />
          Close
        </button>
        <button
          aria-label="Delete position"
          className="px-3 py-2 rounded-md bg-bg-tertiary hover:bg-danger/15 hover:text-danger border border-border/50 text-fg-secondary flex items-center justify-center transition-colors"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}

function StatCell({ label, value, className, compact }) {
  return (
    <div className="bg-bg-primary rounded-md px-2.5 py-2 min-w-0">
      <div className="text-[9px] text-fg-tertiary uppercase tracking-wider mb-1 truncate">
        {label}
      </div>
      <div
        className={cn(
          'text-fg-primary font-medium font-mono truncate',
          compact ? 'text-[11px]' : 'text-[13px]',
          className
        )}
      >
        {value}
      </div>
    </div>
  );
}
