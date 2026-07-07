import type { HTMLAttributes, ReactNode } from 'react'
import { clsx } from '../../lib/cx'

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode
  /** Optional right-aligned "title" node (or use `title` + `subtitle` for the
   *  built-in card header). */
  header?: ReactNode
  /** Compact header title (rendered as small-caps label). */
  title?: string
  /** Optional slot rendered on the right of the built-in title header. */
  actions?: ReactNode
}

/**
 * Bordered surface with padding — the UI kit replacement for the legacy `.card`
 * CSS class. Pages spacing cards vertically should add `mb-3` (or wrap in
 * `space-y-3`) since Card no longer emits margin-bottom itself.
 *
 * Usage:
 *   <Card>content</Card>
 *   <Card title="Platform links" actions={<Button>Split</Button>}>…</Card>
 */
export function Card({ children, className, header, title, actions, ...props }: CardProps) {
  return (
    <div
      className={clsx('rounded-lg border border-border bg-surface p-4', className)}
      {...props}
    >
      {(header || title || actions) && (
        <div className="mb-2 flex items-center justify-between gap-2">
          {header ?? (
            title && (
              <div className="text-sm font-semibold text-text-primary">{title}</div>
            )
          )}
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </div>
  )
}
