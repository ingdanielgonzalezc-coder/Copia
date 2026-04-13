import { cn } from '../lib/utils.js';

export default function KpiCard({ label, value, valueClass, trailing, onClick, active }) {
  return (
    <div
      className={cn(
        'bg-bg-secondary rounded-md px-4 py-3 min-w-0 transition-colors',
        onClick && 'cursor-pointer hover:bg-bg-tertiary',
        active && 'ring-1 ring-accent/40'
      )}
      onClick={onClick}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] text-fg-tertiary uppercase tracking-wider">{label}</div>
        {trailing}
      </div>
      <div className={cn('text-[22px] font-medium text-fg-primary leading-none mt-2 font-mono', valueClass)}>
        {value}
      </div>
    </div>
  );
}
