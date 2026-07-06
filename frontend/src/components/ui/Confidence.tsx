import { clsx } from '../../lib/cx'
import { LABELS } from '../../lib/labels'

/**
 * Shows a same-person score as a plain word + % + a small coloured bar,
 * e.g. "Likely 72%". Bar colour follows the plan's traffic-light rule:
 *   red    < 0.30   (Weak)
 *   amber  < 0.60   (Possible)
 *   green  >= 0.60  (Likely / Very likely)
 *
 * Exported as both `Confidence` (backwards-compat name existing pages import)
 * and `ConfidencePill` (spec name).
 */
export function Confidence({ score }: { score: number | null | undefined }) {
  const { word, pct } = LABELS.confidence(score)
  const s = (score ?? 0)

  // Word colour follows the confidence tone; bar colour follows the plan-doc
  // traffic light so a glance-check reads red/amber/green regardless of word.
  const wordCls =
    s >= 0.6 ? 'text-success' : s >= 0.3 ? 'text-warning' : 'text-text-muted'
  const barCls =
    s >= 0.6 ? 'bg-success' : s >= 0.3 ? 'bg-warning' : 'bg-error'

  return (
    <span className="inline-flex items-center gap-2" title={`${pct}% confidence`}>
      <span className={clsx('text-xs font-medium', wordCls)}>
        {word}{' '}
        <span className="font-mono tabular-nums text-text-muted">{pct}%</span>
      </span>
      <span className="block h-1.5 w-16 overflow-hidden rounded-full bg-border">
        <span className={clsx('block h-full rounded-full', barCls)} style={{ width: `${pct}%` }} />
      </span>
    </span>
  )
}

/** Spec-name alias for the same component. */
export const ConfidencePill = Confidence
