import { clsx } from '../../lib/cx'
import { confidenceWords } from '../../lib/labels'

const tone = {
  success: { text: 'text-success', bar: 'bg-success' },
  warning: { text: 'text-warning', bar: 'bg-warning' },
  muted: { text: 'text-text-muted', bar: 'bg-text-muted' },
}

/** Shows a same-person score as a plain word + % + a small bar, e.g. "Likely 72%". */
export function Confidence({ score }: { score: number | null | undefined }) {
  const { word, pct, tone: t } = confidenceWords(score)
  const c = tone[t]
  return (
    <span className="inline-flex items-center gap-2" title={`${pct}% confidence`}>
      <span className={clsx('text-xs font-medium', c.text)}>
        {word} <span className="font-mono text-text-muted">{pct}%</span>
      </span>
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-border">
        <span className={clsx('block h-full rounded-full', c.bar)} style={{ width: `${pct}%` }} />
      </span>
    </span>
  )
}
