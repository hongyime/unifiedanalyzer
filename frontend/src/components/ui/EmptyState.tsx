import { Inbox } from 'lucide-react'
import type { ReactNode } from 'react'

interface EmptyStateProps {
  icon?: ReactNode
  title?: string
  description?: string
}

/** Friendly empty state — always explains WHY it's empty and what fills it. */
export function EmptyState({
  icon = <Inbox className="w-10 h-10" />,
  title = 'Nothing here yet',
  description = 'This will fill in as the pipeline runs.',
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-text-muted gap-3">
      {icon}
      <p className="text-sm font-medium text-text-secondary">{title}</p>
      <p className="max-w-sm text-center text-xs">{description}</p>
    </div>
  )
}
