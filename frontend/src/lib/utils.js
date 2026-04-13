import clsx from 'clsx';

export function cn(...args) {
  return clsx(...args);
}

// Map verdict actions to color classes
export function verdictStyle(action) {
  switch ((action || '').toUpperCase()) {
    case 'ADD':
    case 'BUY':
    case 'BUY_NEW':
      return 'bg-success/15 text-success';
    case 'TRIM':
    case 'SELL':
      return 'bg-danger/15 text-danger';
    case 'HOLD':
      return 'bg-warning/15 text-warning';
    case 'AVOID':
    case 'AVOID_NEW':
    case 'ABSTAIN':
      return 'bg-bg-tertiary text-fg-secondary';
    case 'BLACKOUT':
      return 'bg-bg-tertiary text-fg-tertiary border border-border/50';
    default:
      return 'bg-bg-tertiary text-fg-secondary';
  }
}

export function phaseStyle(phase) {
  switch ((phase || '').toUpperCase()) {
    case 'EOD':
      return 'bg-accent/15 text-accent';
    case 'INTRADAY':
    case 'INITIAL':
    case 'DISCOVERY':
    default:
      return 'bg-bg-tertiary text-fg-secondary border border-border/50';
  }
}

export function regimeStyle(label) {
  switch ((label || '').toUpperCase()) {
    case 'BULL_TREND':
      return 'bg-success/15 text-success';
    case 'NEUTRAL':
      return 'bg-bg-tertiary text-fg-secondary';
    case 'CORRECTION':
      return 'bg-warning/15 text-warning';
    case 'HIGH_VOLATILITY':
      return 'bg-danger/15 text-danger';
    default:
      return 'bg-bg-tertiary text-fg-secondary';
  }
}

export function pnlClass(value) {
  if (value == null) return 'text-fg-secondary';
  if (value > 0) return 'text-success';
  if (value < 0) return 'text-danger';
  return 'text-fg-secondary';
}
