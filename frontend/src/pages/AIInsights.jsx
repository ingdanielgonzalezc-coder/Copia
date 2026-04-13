import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api, USING_MOCK } from '../lib/api.js';
import { mockRecentDebates, mockStats } from '../lib/mockData.js';
import { fmtPct, fmtDate, fmtTime } from '../lib/format.js';
import { cn, verdictStyle, phaseStyle } from '../lib/utils.js';
import KpiCard from '../components/KpiCard.jsx';

export default function AIInsights() {
  const { debateId } = useParams();
  const navigate = useNavigate();
  const [debates, setDebates] = useState(USING_MOCK ? mockRecentDebates : []);
  const [stats] = useState(mockStats);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    if (USING_MOCK) {
      setDebates(mockRecentDebates);
      return;
    }
    (async () => {
      try {
        const list = await api.getRecentDebates(50);
        setDebates(list);
      } catch (e) {
        console.error(e);
      }
    })();
  }, []);

  useEffect(() => {
    const id = debateId || debates[0]?.id;
    if (!id) return;
    const found = debates.find((d) => d.id === id);
    if (found) setSelected(found);
  }, [debateId, debates]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-[22px] font-medium text-fg-primary leading-tight">AI insights</h1>
        <div className="text-[12px] text-fg-secondary mt-1">
          Debate history, hit rates and cost tracking · Last 30 days
        </div>
      </div>

      <div className="grid grid-cols-5 gap-2.5">
        <KpiCard label="Total debates" value={stats.total_debates} />
        <KpiCard label="Escalation rate" value={`${(stats.escalation_rate * 100).toFixed(0)}%`} />
        <KpiCard
          label="Hit rate · 1w"
          value={`${(stats.hit_rate_1w * 100).toFixed(1)}%`}
          valueClass="text-success"
        />
        <KpiCard label="Avg cost / debate" value={`$${stats.avg_cost.toFixed(2)}`} />
        <KpiCard label="Total cost 30d" value={`$${stats.total_cost_30d.toFixed(2)}`} />
      </div>

      <div className="grid grid-cols-[280px_minmax(0,1fr)] gap-4">
        <div className="bg-bg-secondary rounded-lg p-3">
          <div className="flex gap-2 mb-3">
            <input
              type="text"
              placeholder="Search ticker…"
              className="flex-1 px-3 py-2 bg-bg-primary border border-border/50 rounded text-[12px] text-fg-primary placeholder:text-fg-tertiary focus:outline-none focus:border-accent/50"
            />
          </div>
          <div className="text-[10px] text-fg-tertiary uppercase tracking-wider px-2 pb-2">
            {debates.length} debates
          </div>
          <div className="space-y-1">
            {debates.map((d) => (
              <button
                key={d.id}
                onClick={() => {
                  setSelected(d);
                  navigate(`/insights/${d.id}`);
                }}
                className={cn(
                  'w-full text-left px-3 py-2.5 rounded-md transition-colors',
                  selected?.id === d.id ? 'bg-accent/15' : 'hover:bg-bg-tertiary/50'
                )}
              >
                <div className="flex items-center justify-between mb-1">
                  <div
                    className={cn(
                      'text-[13px] font-medium',
                      selected?.id === d.id ? 'text-accent' : 'text-fg-primary'
                    )}
                  >
                    {d.ticker}
                  </div>
                  <span
                    className={cn(
                      'px-2 py-0.5 rounded-full text-[9px] font-medium',
                      selected?.id === d.id
                        ? 'border border-accent/50 text-accent'
                        : phaseStyle(d.phase)
                    )}
                  >
                    {d.phase}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <div
                    className={cn(
                      'text-[10px] font-mono',
                      selected?.id === d.id ? 'text-accent/70' : 'text-fg-tertiary'
                    )}
                  >
                    {fmtDate(d.timestamp, { short: true })} · {fmtTime(d.timestamp)}
                  </div>
                  <span
                    className={cn(
                      'px-2 py-0.5 rounded-full text-[10px] font-medium',
                      verdictStyle(d.verdict.action)
                    )}
                  >
                    {d.verdict.action} {d.verdict.confidence}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="bg-bg-secondary rounded-lg p-6 min-h-[500px]">
          {selected ? (
            <DebateDetail debate={selected} />
          ) : (
            <div className="text-fg-tertiary text-[13px]">Select a debate to view details</div>
          )}
        </div>
      </div>
    </div>
  );
}

function DebateDetail({ debate }) {
  // Mock-enriched detail. Real version fetches GET /debates/{id} and uses
  // the full Bull/Bear/Judge schema from the backend.
  const isNvda = debate.ticker === 'NVDA';
  const verdictClass = verdictStyle(debate.verdict.action);

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-3">
            <div className="text-[20px] font-medium text-fg-primary">{debate.ticker}</div>
            <span className={cn('px-2 py-0.5 rounded-full text-[10px] font-medium', phaseStyle(debate.phase))}>
              {debate.phase}
            </span>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-bg-tertiary text-fg-secondary border border-border/50">
              v3.2.0
            </span>
          </div>
          <div className="text-[11px] text-fg-tertiary mt-1 font-mono">
            {fmtDate(debate.timestamp, { withTime: true })} ET · debate_id {debate.id}
          </div>
        </div>
        <div className="text-[10px] text-fg-tertiary text-right uppercase tracking-wide leading-relaxed">
          <div>COST $0.35 · LATENCY 124s</div>
          <div>3,240 in / 1,892 out · escalated</div>
        </div>
      </div>

      {/* Judge verdict banner */}
      <div className="bg-warning/10 border border-warning/20 rounded-lg p-5">
        <div className="flex items-center justify-between gap-3 mb-3 flex-wrap">
          <div className="flex items-center gap-3">
            <span className={cn('px-3 py-1 rounded-full text-[12px] font-medium', verdictClass)}>
              {debate.verdict.action} · conf {debate.verdict.confidence}
            </span>
            <div className="text-[11px] text-warning font-medium tracking-wide uppercase">Judge verdict</div>
          </div>
          <div className="text-[10px] text-warning/70 uppercase tracking-wide">
            OPUS 4.6 · THINKING · WEB SEARCH 3/3
          </div>
        </div>
        <div className="text-[13px] leading-relaxed text-warning/90">
          Bull growth thesis is well-supported but Bear timing concerns carry weight given technical
          overextension. Web research confirms SOX sector at ATH and Jensen's $1T AI capex forecast, but
          also surfaces elevated put/call ratio suggesting hedging activity. Decision: hold current 12
          shares. Position is working at +15% — no need to add. Tighten stop to $430 to protect gains.
        </div>
        <div className="mt-3 pt-3 border-t border-warning/20">
          <div className="text-[10px] text-warning/70 uppercase tracking-wide mb-1">
            Notification text · Telegram
          </div>
          <div className="text-[12px] italic text-warning/90">
            "NVDA — HOLD at +15.5%. Stop raised to $430. No action needed."
          </div>
        </div>
        <div className="mt-3 flex gap-5 text-[11px] text-warning/80 flex-wrap">
          <div>
            Final stop: <span className="font-medium font-mono">$430</span>
          </div>
          <div>
            Priority: <span className="font-medium">MEDIUM</span>
          </div>
          <div>
            Catalysts: <span className="font-medium">Q1 earnings May 20</span>
          </div>
        </div>
      </div>

      {/* Allowed actions */}
      <div className="border border-border/50 rounded-md p-4">
        <div className="text-[10px] text-fg-tertiary uppercase tracking-wide font-medium mb-2.5">
          Allowed actions · rules engine pre-filter
        </div>
        <div className="flex flex-wrap gap-2">
          {['HOLD', 'ADD_TO_EXISTING', 'TRIM', 'TIGHTEN_STOP'].map((a) => (
            <span
              key={a}
              className="px-2.5 py-1 rounded bg-bg-tertiary text-[11px] text-fg-secondary border border-border/30 font-mono"
            >
              {a}
            </span>
          ))}
          <span className="px-2.5 py-1 rounded text-[11px] text-fg-tertiary italic">
            BUY_NEW blocked · already held
          </span>
        </div>
      </div>

      {/* Bull + Bear */}
      <div className="grid grid-cols-2 gap-3">
        <div className="border border-border/50 rounded-md p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[10px] text-success font-medium tracking-wide uppercase">
              Bull · Grok 4.2 reasoning
            </div>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-success/15 text-success">
              ADD 87
            </span>
          </div>
          <div className="text-[11px] leading-relaxed text-fg-primary">
            Q4 guidance raised 18% above consensus. Data center revenue +94% YoY confirms unprecedented
            AI infrastructure demand. Jensen Huang's $1T AI capex forecast signals multi-year runway.
            Position +15% with strong sector momentum — SOX at ATH. Analyst consensus target $620 (+22.7%
            upside) across 56 analysts, strong_buy. Recommend +2-3% allocation on pullback below $490.
          </div>
        </div>
        <div className="border border-border/50 rounded-md p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[10px] text-danger font-medium tracking-wide uppercase">
              Bear · Sonnet 4.6
            </div>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-warning/15 text-warning">
              HOLD 22
            </span>
          </div>
          <div className="text-[11px] leading-relaxed text-fg-primary">
            RSI 78 signals near-term overbought conditions and mean-reversion risk. 13F filings show
            reduced hedge fund net long exposure quarter-over-quarter. Earnings in 38 days creates
            forward blackout constraint. Fundamentals remain intact but entry timing is suboptimal —
            prefer holding existing exposure without adding until pullback or post-earnings clarity.
          </div>
        </div>
      </div>

      {/* Snapshot + data_gaps side by side */}
      <div className="grid grid-cols-[minmax(0,1fr)_280px] gap-3">
        <div className="border border-border/50 rounded-md p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="text-[10px] text-fg-tertiary uppercase tracking-wide font-medium">
              Snapshot · context used
            </div>
            <div className="text-[10px] text-fg-tertiary">Polygon · yfinance</div>
          </div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-[11px]">
            {[
              ['Price', '$505.00'],
              ['RSI(14)', '78.3'],
              ['ATR %', '4.2%'],
              ['52W range', '82%'],
              ['Target mean', '$620 (+22.7%)'],
              ['Analysts', '56 · strong_buy'],
              ['Short % float', '1.2%'],
              ['Earnings in', '38 days']
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between">
                <span className="text-fg-tertiary">{k}</span>
                <span className="text-fg-primary font-medium font-mono">{v}</span>
              </div>
            ))}
          </div>
          <div className="mt-3 pt-3 border-t border-border/50 flex gap-5 text-[11px] text-fg-secondary flex-wrap">
            <div>
              Macro: <span className="text-success font-medium">BULL_TREND</span>
            </div>
            <div>
              VIX: <span className="text-fg-primary font-medium font-mono">14.2</span>
            </div>
            <div>
              SPY 4w: <span className="text-success font-medium font-mono">+2.7%</span>
            </div>
            <div>
              Sector: <span className="text-fg-primary font-medium">Semis p75</span>
            </div>
          </div>
        </div>

        <div className="border border-warning/30 rounded-md p-4">
          <div className="text-[10px] text-warning uppercase tracking-wide font-medium mb-2.5">
            Data gaps · declared by judge
          </div>
          <ul className="space-y-1.5 text-[11px] text-fg-primary">
            <li className="flex gap-2">
              <span className="text-warning">·</span>
              <span>No 13F data for Q1 2026 yet</span>
            </li>
            <li className="flex gap-2">
              <span className="text-warning">·</span>
              <span>Options flow unavailable via current feed</span>
            </li>
            <li className="flex gap-2">
              <span className="text-warning">·</span>
              <span>Insider transactions stale &gt;14 days</span>
            </li>
          </ul>
        </div>
      </div>

      {/* Web search results */}
      <div className="border border-border/50 rounded-md p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="text-[10px] text-fg-tertiary uppercase tracking-wide font-medium">
            Web search · 3 queries
          </div>
          <div className="text-[10px] text-fg-tertiary">Opus native tool</div>
        </div>
        <div className="space-y-2.5 text-[11px]">
          {[
            {
              n: 1,
              title: 'SOX semiconductor index at all-time high on AI spend',
              src: 'techcrunch.com · Apr 10'
            },
            {
              n: 2,
              title: 'Jensen Huang forecasts $1T AI data center capex by 2028',
              src: 'reuters.com · Apr 9'
            },
            {
              n: 3,
              title: 'Hedge fund 13F filings show NVDA net long exposure down QoQ',
              src: 'bloomberg.com · Apr 8'
            }
          ].map((r) => (
            <div key={r.n} className="flex gap-2.5 items-start">
              <div className="w-4 h-4 rounded-full bg-accent/15 text-accent text-[9px] flex items-center justify-center font-medium shrink-0 mt-0.5">
                {r.n}
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-fg-primary">{r.title}</div>
                <div className="text-fg-tertiary text-[10px] mt-0.5">{r.src}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
