import { USING_MOCK } from '../lib/api.js';

export default function Settings() {
  return (
    <div className="space-y-5 max-w-2xl">
      <div>
        <h1 className="text-[22px] font-medium text-fg-primary leading-tight">Settings</h1>
        <div className="text-[12px] text-fg-secondary mt-1">System configuration (read-only)</div>
      </div>

      <div className="bg-bg-secondary rounded-lg p-5 space-y-4">
        <Row label="API mode" value={USING_MOCK ? 'Mock data (not connected)' : 'Live · Railway backend'} />
        <Row label="API base" value={import.meta.env.VITE_API_BASE || '— set VITE_API_BASE in .env.local'} mono />
        <Row label="Prompt version" value="v3.2.0" mono />
        <Row label="Models" value="Bull: Grok 4.2 · Bear: Sonnet 4.6 · Judge: Sonnet/Opus 4.6" />
        <Row label="Daily cost cap" value="$5.00" mono />
        <Row label="Max alloc / ticker" value="10% (12% override)" />
        <Row label="Max alloc / sector" value="25%" />
        <Row label="Earnings blackout" value="3d before · 1d after" />
      </div>

      <div className="text-[11px] text-fg-tertiary">
        Edit these values in <span className="font-mono text-fg-secondary">config/rules.yaml</span> and
        <span className="font-mono text-fg-secondary"> config/investor_profile.yaml</span> on the backend.
      </div>
    </div>
  );
}

function Row({ label, value, mono }) {
  return (
    <div className="flex justify-between items-start gap-4 py-2 border-b border-border/30 last:border-0">
      <div className="text-[12px] text-fg-secondary">{label}</div>
      <div className={`text-[12px] text-fg-primary text-right ${mono ? 'font-mono' : ''}`}>{value}</div>
    </div>
  );
}
