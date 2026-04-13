// Mock data based on the smoke tests from plan v3.3.
// Used when VITE_API_BASE / VITE_API_KEY are not set in .env.local.

export const mockPortfolio = {
  total_value: 52847,
  invested: 44580,
  cash: 8267,
  gain_loss_total_pct: 5.69,
  gain_loss_invested_pct: 6.73,
  position_count: 8,
  regime: { label: 'BULL_TREND', vix: 14.2 },
  win_rate: { '1d': 0.612, '1w': 0.684, '1m': 0.657, all: 0.641 },
  updated_at: '2026-04-11T14:32:00-04:00'
};

export const mockPositions = [
  {
    ticker: 'NVDA',
    name: 'NVIDIA Corp',
    sector: 'Semiconductors',
    shares: 12,
    buy_price: 420.0,
    current_price: 505.0,
    purchase_date: '2026-03-02',
    stop_loss: 430,
    stop_win: 565,
    value: 6060,
    allocation_pct: 11.47,
    pnl_pct: 20.24,
    last_verdict: { action: 'HOLD', confidence: 72, days_ago: 1, blackout: false }
  },
  {
    ticker: 'COST',
    name: 'Costco',
    sector: 'Consumer Staples',
    shares: 6,
    buy_price: 850.0,
    current_price: 925.0,
    purchase_date: '2026-03-15',
    stop_loss: 810,
    stop_win: 980,
    value: 5550,
    allocation_pct: 10.5,
    pnl_pct: 8.82,
    last_verdict: { action: 'HOLD', confidence: 68, days_ago: 5, blackout: false }
  },
  {
    ticker: 'JPM',
    name: 'JPMorgan Chase',
    sector: 'Financials',
    shares: 28,
    buy_price: 195.0,
    current_price: 208.0,
    purchase_date: '2026-02-20',
    stop_loss: 190,
    stop_win: 230,
    value: 5824,
    allocation_pct: 11.02,
    pnl_pct: 6.67,
    last_verdict: { action: 'BLACKOUT', confidence: null, days_ago: 12, blackout: true }
  },
  {
    ticker: 'LYV',
    name: 'Live Nation',
    sector: 'Communication Services',
    shares: 38,
    buy_price: 138.0,
    current_price: 147.0,
    purchase_date: '2026-03-08',
    stop_loss: 130,
    stop_win: 165,
    value: 5586,
    allocation_pct: 10.57,
    pnl_pct: 6.52,
    last_verdict: { action: 'HOLD', confidence: 70, days_ago: 2, blackout: false }
  },
  {
    ticker: 'TRGP',
    name: 'Targa Resources',
    sector: 'Energy',
    shares: 32,
    buy_price: 170.0,
    current_price: 184.0,
    purchase_date: '2026-03-20',
    stop_loss: 162,
    stop_win: 205,
    value: 5888,
    allocation_pct: 11.14,
    pnl_pct: 8.24,
    last_verdict: { action: 'ADD', confidence: 74, days_ago: 1, blackout: false }
  },
  {
    ticker: 'XOM',
    name: 'Exxon Mobil',
    sector: 'Energy',
    shares: 52,
    buy_price: 108.0,
    current_price: 114.0,
    purchase_date: '2026-02-28',
    stop_loss: 102,
    stop_win: 128,
    value: 5928,
    allocation_pct: 11.22,
    pnl_pct: 5.56,
    last_verdict: { action: 'HOLD', confidence: 66, days_ago: 6, blackout: false }
  },
  {
    ticker: 'AAPL',
    name: 'Apple Inc.',
    sector: 'Hardware',
    shares: 32,
    buy_price: 175.0,
    current_price: 180.0,
    purchase_date: '2026-03-12',
    stop_loss: 165,
    stop_win: 195,
    value: 5760,
    allocation_pct: 10.9,
    pnl_pct: 2.86,
    last_verdict: { action: 'HOLD', confidence: 64, days_ago: 4, blackout: false }
  },
  {
    ticker: 'MSFT',
    name: 'Microsoft',
    sector: 'Software',
    shares: 12,
    buy_price: 466.0,
    current_price: 415.5,
    purchase_date: '2026-02-10',
    stop_loss: 395,
    stop_win: 520,
    value: 4986,
    allocation_pct: 9.43,
    pnl_pct: -10.84,
    last_verdict: { action: 'TRIM', confidence: 60, days_ago: 1, blackout: false }
  }
];

export const mockWatchlist = [
  { ticker: 'COST', name: 'Costco', sector: 'Consumer', score: 93.0, price: 892.15, change_1d_pct: 1.24, news_flag: false },
  { ticker: 'LYV', name: 'Live Nation', sector: 'Media', score: 93.0, price: 142.78, change_1d_pct: 0.67, news_flag: false },
  { ticker: 'TRGP', name: 'Targa Resources', sector: 'Energy', score: 92.3, price: 178.42, change_1d_pct: -0.31, news_flag: false },
  { ticker: 'MSFT', name: 'Microsoft', sector: 'Software', score: 88.5, price: 415.5, change_1d_pct: 0.82, news_flag: true }
];

export const mockRecentDebates = [
  {
    id: 'a3f7e29c',
    ticker: 'NVDA',
    phase: 'EOD',
    verdict: { action: 'HOLD', confidence: 72 },
    timestamp: '2026-04-11T21:04:00-04:00',
    summary: 'Overbought · RSI 78 · Jensen $1T forecast via web search'
  },
  {
    id: 'b2e8d4a1',
    ticker: 'MSFT',
    phase: 'EOD',
    verdict: { action: 'TRIM', confidence: 60 },
    timestamp: '2026-04-11T21:02:00-04:00',
    summary: 'Position -10.87% · escalated to Opus · stop $395'
  },
  {
    id: 'c1d9f3b2',
    ticker: 'JPM',
    phase: 'INTRADAY',
    verdict: { action: 'HOLD', confidence: 64 },
    timestamp: '2026-04-11T14:21:00-04:00',
    summary: 'Earnings blackout · 3 days to report · no action allowed'
  },
  {
    id: 'd4c6a8e9',
    ticker: 'AAPL',
    phase: 'INITIAL',
    verdict: { action: 'AVOID', confidence: 62 },
    timestamp: '2026-04-11T09:47:00-04:00',
    summary: 'Conservative market · no entry catalyst · Bull/Bear split'
  },
  {
    id: 'e5b7d2f6',
    ticker: 'COST',
    phase: 'EOD',
    verdict: { action: 'HOLD', confidence: 68 },
    timestamp: '2026-04-10T21:08:00-04:00',
    summary: 'Strong fundamentals · membership growth on track'
  }
];

// Portfolio vs SPY — 30 daily points, percent return from day 0
export const mockPerformanceSeries = (() => {
  const days = 30;
  const out = [];
  let p = 0, s = 0;
  for (let i = 0; i < days; i++) {
    p += (Math.sin(i / 4) * 0.3) + 0.22 + (Math.random() - 0.4) * 0.25;
    s += (Math.sin(i / 5) * 0.25) + 0.09 + (Math.random() - 0.4) * 0.2;
    const date = new Date(2026, 2, 13 + i);
    out.push({
      date: date.toISOString().slice(0, 10),
      portfolio: Math.max(0, p),
      spy: Math.max(0, s)
    });
  }
  // Force ending values close to our KPIs
  out[out.length - 1].portfolio = 5.69;
  out[out.length - 1].spy = 2.7;
  return out;
})();

export const mockStats = {
  total_debates: 47,
  escalation_rate: 0.75,
  hit_rate_1w: 0.684,
  avg_cost: 0.21,
  total_cost_30d: 9.87
};

// Seeded PRNG so each ticker's history is stable across renders
function seededRandom(seed) {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

// Synthetic 120-day price history ending at current_price, walking backward
// with gentle drift toward buy_price. Deterministic per ticker.
export function mockPriceHistory(position, days = 120) {
  const seed = position.ticker.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
  const rng = seededRandom(seed);
  const series = [];
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  let price = position.current_price;
  for (let i = 0; i < days; i++) {
    const date = new Date(today);
    date.setDate(date.getDate() - i);
    series.unshift({ date: date.toISOString().slice(0, 10), price: Math.max(0.01, price) });
    const drift = (position.buy_price - price) * 0.012;
    const noise = (rng() - 0.5) * 0.028 * price * 2;
    price = price - drift - noise;
  }
  return series;
}
