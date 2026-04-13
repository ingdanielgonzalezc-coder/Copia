// API client for Stock Advisor FastAPI backend.
// Configure VITE_API_BASE and VITE_API_KEY in .env.local
// If not configured, the app runs with mock data so the UI is viewable immediately.

const API_BASE = import.meta.env.VITE_API_BASE || '';
const API_KEY = import.meta.env.VITE_API_KEY || '';

export const USING_MOCK = !API_BASE || !API_KEY;

async function request(path, options = {}) {
  if (USING_MOCK) {
    throw new Error('MOCK_MODE');
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': API_KEY,
      ...(options.headers || {})
    }
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`API ${res.status}: ${text || res.statusText}`);
  }
  return res.json();
}

export const api = {
  // Portfolio
  getPortfolio: () => request('/portfolio'),
  getPositions: () => request('/portfolio/positions'),
  getWatchlist: () => request('/portfolio/watchlist'),
  createPosition: (body) =>
    request('/portfolio/positions', { method: 'POST', body: JSON.stringify(body) }),
  updatePosition: (ticker, body) =>
    request(`/portfolio/positions/${ticker}`, { method: 'PATCH', body: JSON.stringify(body) }),
  closePosition: (ticker, body) =>
    request(`/portfolio/positions/${ticker}/close`, { method: 'POST', body: JSON.stringify(body) }),
  deletePosition: (ticker) =>
    request(`/portfolio/positions/${ticker}`, { method: 'DELETE' }),

  // Debates
  getRecentDebates: (limit = 20) => request(`/debates/recent?limit=${limit}`),
  getDebate: (id) => request(`/debates/${id}`),
  triggerDebate: (body) =>
    request('/debates/trigger', { method: 'POST', body: JSON.stringify(body) }),

  // Cycles
  triggerIntraday: () => request('/cycles/intraday', { method: 'POST' }),
  triggerEod: () => request('/cycles/eod', { method: 'POST' }),

  // Data
  getBlackout: () => request('/earnings/blackout'),
  getStats: (days = 30) => request(`/stats?days=${days}`),
  getHealth: () => request('/health'),
  getTask: (id) => request(`/tasks/${id}`)
};
