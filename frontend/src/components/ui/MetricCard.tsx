import { clsx } from '../../lib/cx'
import type { ReactNode } from 'react'
import { InfoTip } from './InfoTip'

interface MetricCardProps {
  label: string
  value: string | number
  sublabel?: string
  icon?: ReactNode
  status?: 'success' | 'error' | 'warning' | 'info' | 'idle'
  /** Optional plain-language explanation shown as a (?) tooltip. */
  help?: string
}

const dots: Record<string, string> = {
  success: 'bg-success',
  error: 'bg-error',
  warning: 'bg-warning',
  info: 'bg-info',
  idle: 'bg-text-muted',
}

export function MetricCard({ label, value, sublabel, icon, status, help }: MetricCardProps) {
  return (
    <div className="bg-surface rounded-lg border border-border p-4">
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <p className="flex items-center gap-1 text-xs uppercase tracking-wider text-text-muted">
            {label}
            {help && <InfoTip text={help} />}
          </p>
          <p className="mt-1 text-2xl font-semibold font-mono">
            {typeof value === 'number' ? value.toLocaleString() : value}
          </p>
          {sublabel && <p className="mt-1 text-xs text-text-muted">{sublabel}</p>}
        </div>
        <div className="flex items-center gap-2">
          {status && <div className={clsx('w-2 h-2 rounded-full', dots[status])} />}
          {icon && <div className="text-text-secondary">{icon}</div>}
        </div>
      </div>
    </div>
  )
}
