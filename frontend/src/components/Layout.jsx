import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar.jsx';
import { USING_MOCK } from '../lib/api.js';

export default function Layout() {
  return (
    <div className="flex h-full min-h-screen bg-bg-primary text-fg-primary">
      <Sidebar />
      <main className="flex-1 min-w-0 overflow-auto">
        {USING_MOCK && (
          <div className="bg-warning/10 border-b border-warning/20 px-6 py-2 text-[11px] text-warning flex items-center justify-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-warning" />
            Mock data mode — set VITE_API_BASE and VITE_API_KEY in .env.local to connect to Railway
          </div>
        )}
        <div className="px-8 py-7 max-w-[1440px] mx-auto">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
