import { NavLink } from 'react-router'
import { clsx } from '../../lib/cx'
import {
  ListChecks, GitCompare, Users, Network, Bell, Images, ScanFace, Search,
  FolderOpen, Play, HelpCircle, Database, Languages, Gauge, GitFork,
  ShieldCheck,
} from 'lucide-react'
import { Logo } from './Logo'
import type { LiveHealth } from '../../api'

const groups = [
  {
    label: 'Investigate',
    items: [
      { to: '/', label: 'Triage', icon: ListChecks, end: true },
      { to: '/review', label: 'Review', icon: GitCompare },
      { to: '/entities', label: 'People', icon: Users },
      { to: '/communities', label: 'Communities', icon: Network },
      { to: '/graph', label: 'Graph', icon: GitFork },
    ],
  },
  {
    label: 'Evidence',
    items: [
      { to: '/alerts', label: 'Alerts', icon: Bell },
      { to: '/search', label: 'Search', icon: Search },
      { to: '/media', label: 'Media', icon: Images },
      { to: '/faces', label: 'Faces', icon: ScanFace },
    ],
  },
  {
    label: 'Operations',
    items: [
      { to: '/production', label: 'Production', icon: ShieldCheck },
      { to: '/collector', label: 'Collector', icon: Database },
      { to: '/multilingual', label: 'Languages', icon: Languages },
      { to: '/eval', label: 'Evaluation', icon: Gauge },
    ],
  },
  {
    label: 'Workspace',
    items: [
      { to: '/cases', label: 'Cases', icon: FolderOpen },
      { to: '/runs', label: 'Runs', icon: Play },
      { to: '/help', label: 'Help', icon: HelpCircle },
    ],
  },
]

function StatusPill({ health }: { health: LiveHealth | null }) {
  const ok = health?.status === 'ok'
  const dot = !health ? 'bg-text-muted' : ok ? 'bg-success' : 'bg-warning'
  return (
    <div
      className="mt-2 flex items-center gap-1.5"
      title={!health ? 'Connecting…' : ok ? 'System healthy' : 'System degraded'}
    >
      <span className={clsx('h-2 w-2 rounded-full', dot, health && 'animate-pulse')} />
      <span className="text-[10px] tabular-nums text-text-secondary">
        {!health ? 'connecting…' : `${health.entity_count.toLocaleString()} people tracked`}
      </span>
    </div>
  )
}

export function Sidebar({ health }: { health: LiveHealth | null }) {
  return (
    <aside className="fixed bottom-0 left-0 top-0 flex w-52 flex-col border-r border-border bg-surface">
      <div className="border-b border-border p-4">
        <div className="flex items-center gap-2.5">
          <Logo />
          <div className="min-w-0">
            <h1 className="text-sm font-semibold leading-tight tracking-wide text-text-primary">
              UnifiedAnalyzer
            </h1>
            <p className="text-[10px] text-text-muted">Who is who</p>
          </div>
        </div>
        <StatusPill health={health} />
      </div>

      <nav className="flex-1 overflow-y-auto p-3">
        {groups.map((group) => (
          <div key={group.label} className="mb-3">
            <p className="px-3 py-1 text-[10px] font-medium uppercase tracking-wider text-text-muted">
              {group.label}
            </p>
            <div className="space-y-0.5">
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    clsx(
                      'flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors',
                      isActive
                        ? 'bg-white font-medium text-black'
                        : 'text-text-secondary hover:bg-white/5 hover:text-text-primary',
                    )
                  }
                >
                  <item.icon className="h-4 w-4" />
                  {item.label}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="flex items-center gap-1 border-t border-border p-3 text-[10px] text-text-muted">
        <kbd className="rounded border border-border px-1.5 py-0.5">⌘K</kbd>
        <span>to search</span>
      </div>
    </aside>
  )
}
