export function fmtCurrency(value, opts = {}) {
  if (value == null || isNaN(value)) return '—';
  const compact = opts.compact && Math.abs(value) >= 10000;
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: opts.decimals ?? 0,
    maximumFractionDigits: opts.decimals ?? 0,
    notation: compact ? 'compact' : 'standard'
  }).format(value);
}

export function fmtPct(value, opts = {}) {
  if (value == null || isNaN(value)) return '—';
  const decimals = opts.decimals ?? 2;
  const sign = opts.signed && value > 0 ? '+' : '';
  return `${sign}${value.toFixed(decimals)}%`;
}

export function fmtNumber(value, decimals = 0) {
  if (value == null || isNaN(value)) return '—';
  return value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals
  });
}

export function fmtDate(iso, opts = {}) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (opts.short) {
    return d.toLocaleDateString('en-US', { month: 'short', day: '2-digit' });
  }
  if (opts.withTime) {
    return d.toLocaleString('en-US', {
      month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
    });
  }
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: '2-digit' });
}

export function fmtTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: '2-digit', minute: '2-digit', hour12: false
  });
}

export function fmtRelativeDays(days) {
  if (days == null) return '—';
  if (days === 0) return 'today';
  if (days === 1) return '1d ago';
  return `${days}d ago`;
}
