import { cn } from '../lib/utils.js';

export default function Chip({ children, className, variant = 'default' }) {
  const variants = {
    default: 'bg-bg-tertiary text-fg-secondary',
    success: 'bg-success/15 text-success',
    danger: 'bg-danger/15 text-danger',
    warning: 'bg-warning/15 text-warning',
    info: 'bg-accent/15 text-accent',
    outline: 'border border-border/50 text-fg-secondary'
  };
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium tracking-wide whitespace-nowrap',
        variants[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
