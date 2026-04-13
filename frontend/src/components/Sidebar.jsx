import { NavLink } from 'react-router-dom';
import { LineChart, Briefcase, MessageSquareText, Settings as SettingsIcon } from 'lucide-react';
import { cn } from '../lib/utils.js';

const items = [
  { to: '/dashboard', icon: LineChart, label: 'Dashboard' },
  { to: '/portfolio', icon: Briefcase, label: 'Portfolio' },
  { to: '/insights', icon: MessageSquareText, label: 'AI insights' }
];

// No visual tooltips — screen readers still get the name via aria-label.
// On touch devices any hover-triggered tooltip tends to stick, so we drop them entirely.
export default function Sidebar() {
  return (
    <aside className="w-16 shrink-0 bg-bg-primary border-r border-border/50 flex flex-col items-center py-4 gap-1">
      {items.map(({ to, icon: Icon, label }) => (
        <NavLink
          key={to}
          to={to}
          aria-label={label}
          className={({ isActive }) =>
            cn(
              'w-11 h-11 rounded-md flex items-center justify-center transition-colors',
              isActive
                ? 'bg-accent/15 text-accent'
                : 'text-fg-tertiary hover:text-fg-primary hover:bg-bg-tertiary'
            )
          }
        >
          <Icon className="w-5 h-5" />
        </NavLink>
      ))}

      <div className="flex-1" />

      <NavLink
        to="/settings"
        aria-label="Settings"
        className={({ isActive }) =>
          cn(
            'w-11 h-11 rounded-md flex items-center justify-center transition-colors',
            isActive
              ? 'bg-accent/15 text-accent'
              : 'text-fg-tertiary hover:text-fg-primary hover:bg-bg-tertiary'
          )
        }
      >
        <SettingsIcon className="w-5 h-5" />
      </NavLink>
    </aside>
  );
}
