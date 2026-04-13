import { cn } from '../lib/utils.js';

export function Card({ className, children, ...props }) {
  return (
    <div className={cn('bg-bg-secondary rounded-lg', className)} {...props}>
      {children}
    </div>
  );
}

export function CardHeader({ title, subtitle, action, className }) {
  return (
    <div className={cn('flex items-start justify-between gap-3 mb-3', className)}>
      <div className="min-w-0">
        <div className="text-[13px] font-medium text-fg-primary">{title}</div>
        {subtitle && <div className="text-[11px] text-fg-tertiary mt-0.5">{subtitle}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}
