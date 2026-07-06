import { clsx } from '../../lib/cx'

interface SkeletonLoaderProps {
  rows?: number
  className?: string
}

/** Animated placeholder bars — feels lighter than a spinner for list-shaped
 *  loading (tables, feeds, cards). */
export function SkeletonLoader({ rows = 3, className }: SkeletonLoaderProps) {
  return (
    <div className={clsx('space-y-3', className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-4 animate-pulse rounded bg-white/5"
          // Slight per-row width variance so the skeleton doesn't read as a
          // stack of identical bars.
          style={{ width: `${70 + ((i * 13) % 30)}%` }}
        />
      ))}
    </div>
  )
}
