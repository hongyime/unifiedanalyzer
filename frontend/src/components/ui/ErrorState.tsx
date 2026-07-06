import { AlertTriangle } from 'lucide-react'
import { clsx } from '../../lib/cx'
import { Button } from './Button'

interface ErrorStateProps {
  message?: string
  onRetry?: () => void
  className?: string
}

/** Friendly error card. Prefer this over surfacing raw exception strings. */
export function ErrorState({
  message = 'Something went wrong',
  onRetry,
  className,
}: ErrorStateProps) {
  return (
    <div className={clsx('flex flex-col items-center justify-center gap-3 py-16 text-error', className)}>
      <AlertTriangle className="h-10 w-10" />
      <p className="text-sm font-medium">{message}</p>
      {onRetry && (
        <Button variant="ghost" size="sm" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  )
}
