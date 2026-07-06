import type { ReactNode } from 'react'
import { Sidebar } from './layout/Sidebar'
import { Header } from './layout/Header'
import type { LiveHealth } from '../api'

/**
 * The single layout shell every page renders inside. Composes the fixed
 * left `Sidebar` (grouped nav) with the sticky top `Header` (pipeline pill
 * + help link) and yields the routed page content in `children`.
 *
 * Live health is passed in from `App` so both the sidebar (coverage pill)
 * and the header (pipeline pill) share one websocket subscription.
 */
export function AppShell({
  health,
  children,
}: {
  health: LiveHealth | null
  children: ReactNode
}) {
  return (
    <div className="min-h-screen bg-background">
      <Sidebar health={health} />
      <main className="ml-52 flex min-h-screen flex-col">
        <Header health={health} />
        <div className="flex-1 p-6">
          <div className="mx-auto max-w-7xl">{children}</div>
        </div>
      </main>
    </div>
  )
}
