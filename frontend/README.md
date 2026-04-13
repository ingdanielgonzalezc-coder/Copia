# Stock Advisor — Frontend

React dashboard for the multi-agent stock advisory system. Dark-mode financial UI built with Vite, React 18, Tailwind, and react-router-dom. Installable as a PWA on desktop and Samsung Tab S9+.

## Stack

- **Vite 5** + **React 18**
- **Tailwind CSS** with custom dark theme tokens
- **react-router-dom** for routing
- **vite-plugin-pwa** for offline-capable installable app
- **lucide-react** for icons
- Fonts: **Satoshi** (UI) + **JetBrains Mono** (tabular numbers)

No shadcn/ui, no chart library — components and the Portfolio-vs-SPY chart are hand-rolled for full control of the design tokens.

## Pages

| Route | Page | Status |
|---|---|---|
| `/dashboard` | Dashboard — KPIs, regime badge, perf chart, watchlist preview, top positions, recent debates | ✅ Complete |
| `/portfolio` | Portfolio — tabs (Holdings / Watchlist / Earnings), full table, add/edit/close/delete actions | ⚠️ Holdings table complete. Watchlist and Earnings calendar stubs. Action modals pending. |
| `/insights` | AI Insights — stats row, master-detail debate viewer with Judge banner, Bull/Bear, allowed_actions, data_gaps, snapshot, web search | ✅ Complete with mock data. Real API hookup pending `GET /debates/{id}` with full schema. |
| `/insights/:debateId` | Same as above with direct link | ✅ |
| `/settings` | Read-only config view | ✅ |

## Run locally

```bash
cd frontend
npm install
npm run dev
```

Opens at `http://localhost:5173`. **Runs in mock data mode by default** — you see the full UI with realistic data from the v3.3 smoke tests (NVDA HOLD 72, MSFT TRIM 60, JPM blackout, etc).

## Connect to the Railway backend

```bash
cp .env.example .env.local
# Edit .env.local and set:
#   VITE_API_BASE=https://web-production-a7e41.up.railway.app
#   VITE_API_KEY=<your ADVISOR_API_KEY from Railway>
npm run dev
```

When both env vars are set, the mock banner disappears and the app fetches real data from FastAPI endpoints via the `X-API-Key` header.

### Backend endpoints currently consumed

- `GET /portfolio`
- `GET /portfolio/positions`
- `GET /portfolio/watchlist`
- `GET /debates/recent?limit=N`
- `GET /debates/{id}`
- `GET /stats?days=30`

### Backend endpoints needed for full CRUD on Portfolio (not yet in the API)

- `POST /portfolio/positions` — create position (buy)
- `PATCH /portfolio/positions/{ticker}` — partial buy/sell (edit)
- `POST /portfolio/positions/{ticker}/close` — close position (sell all, keep record)
- `DELETE /portfolio/positions/{ticker}` — hard delete (error correction only)

These are already wired on the frontend in `src/lib/api.js` — they'll just return 404 until the backend adds them.

## Build for production

```bash
npm run build
```

Output goes to `dist/`. Deploy that folder to Vercel, Netlify, or any static host.

## Deploy to Vercel

```bash
npm i -g vercel
vercel
```

In the Vercel project settings, add the environment variables:

- `VITE_API_BASE` = `https://web-production-a7e41.up.railway.app`
- `VITE_API_KEY` = your Advisor API key

Then `vercel --prod` for production deploy.

## Install as a PWA

**Desktop (Chrome/Edge):** visit the deployed URL → look for the install icon in the address bar → "Install Stock Advisor". Opens in its own window without browser chrome.

**Samsung Tab S9+ (Chrome):** visit the deployed URL → tap the three-dot menu → "Install app" or "Add to Home screen". The app appears in your launcher and opens fullscreen. Theme color matches the dark navy background, status bar blends in.

**Note on icons:** You need to add `icon-192.png`, `icon-512.png`, and `apple-touch-icon.png` to the `public/` folder before deploying for the PWA manifest to work fully. The current build references them but they're not shipped in this scaffold — generate them from the favicon SVG or design them separately. Use a maskable icon generator (e.g. maskable.app) so Samsung's launcher crops them correctly.

## File structure

```
frontend/
├── .env.example
├── .gitignore
├── index.html
├── package.json
├── postcss.config.js
├── tailwind.config.js
├── vite.config.js
├── public/
│   └── favicon.svg
└── src/
    ├── main.jsx                    # Entry
    ├── App.jsx                     # Router
    ├── index.css                   # Theme tokens + Tailwind
    ├── lib/
    │   ├── api.js                  # FastAPI client with X-API-Key
    │   ├── mockData.js             # Fallback data for mock mode
    │   ├── format.js               # Currency, pct, date formatters
    │   └── utils.js                # cn, verdictStyle, pnlClass, regimeStyle
    ├── components/
    │   ├── Layout.jsx              # Sidebar + outlet wrapper
    │   ├── Sidebar.jsx             # Collapsed 64px icon rail
    │   ├── Card.jsx                # Surface primitive
    │   ├── Chip.jsx                # Pill primitive
    │   ├── KpiCard.jsx             # Metric card
    │   ├── RegimeBadge.jsx         # Live market regime pill
    │   └── PerformanceChart.jsx    # SVG Portfolio vs SPY line chart
    └── pages/
        ├── Dashboard.jsx           # Main view
        ├── Portfolio.jsx           # Holdings table + tab stubs
        ├── AIInsights.jsx          # Master-detail debate viewer
        └── Settings.jsx            # Read-only config
```

## Design tokens

All colors defined as CSS variables in `src/index.css`, consumed via Tailwind in `tailwind.config.js`:

- `bg-primary` deep navy-black — page background
- `bg-secondary` — card surface
- `bg-tertiary` — elevated/hover
- `fg-primary` / `secondary` / `tertiary` — text hierarchy
- `accent` violet #a78bfa — primary UI accent, active states, portfolio line
- `success` green / `danger` red / `warning` amber / `info` blue — semantic only

**P&L is strictly green/red.** Accent violet never encodes gain/loss — it's only for navigation, active states, and the portfolio chart line.

## Known limitations

- Mock data charts use synthetic series — real backend does not yet return a time series. Add a `GET /portfolio/history?days=30` endpoint.
- Action modals (Add, Edit, Close, Delete) are not implemented — the button exists but doesn't open anything yet.
- Watchlist tab and Earnings calendar tab in Portfolio are placeholder stubs.
- PWA icon files (192/512 PNG, apple-touch-icon) need to be added before deploy.
- No authentication flow — relies on VITE_API_KEY baked at build time. For personal single-user use this is fine.
