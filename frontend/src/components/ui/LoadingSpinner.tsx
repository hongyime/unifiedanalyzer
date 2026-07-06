import { Loader2 } from 'lucide-react'
import { clsx } from '../../lib/cx'

interface LoadingSpinnerProps {
  label?: string
  className?: string
}

/** Small centred loading indicator — used in place of "Loading…" ad-hoc text. */
export function LoadingSpinner({ label = 'Loading…', className }: LoadingSpinnerProps) {
  return (
    <div className={clsx('flex flex-col items-center justify-center gap-3 py-12 text-text-muted', className)}>
      <Loader2 className="h-6 w-6 animate-spin" />
      <span className="text-sm">{label}</span>
    </div>
  )
}
