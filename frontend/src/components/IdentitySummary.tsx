import { EntityDetail } from '../api'
import { Card } from './ui/Card'
import { PlatformBadge } from './ui/PlatformBadge'

/**
 * "Best guess at who this is" — fuses the hard identifiers we can already reach
 * (emails/phones/websites/reused handles extracted from identity_signals) plus
 * the cross-platform handle chips, with provenance implied by signal type.
 * Makes identity the headline rather than implicit.
 *
 * TODO(collector schema): add OSINT real-name anchors (yearbook/dean's-list/
 * obituary matches) as a loud real-name badge once the collector exposes them.
 */
export function IdentitySummary({ entity }: { entity: EntityDetail }) {
  const sigs = entity.identity_signals || []
  const pick = (t: string) =>
    Array.from(new Set(sigs.filter((s) => s.signal_type === t && s.value).map((s) => s.value)))
  const reuse = Array.from(
    new Set(sigs.filter((s) => ['username_exact', 'cross_platform_link'].includes(s.signal_type) && s.value).map((s) => s.value)),
  )

  const rows: [string, string[]][] = ([
    ['Emails', pick('email_match')],
    ['Phones', pick('phone_match')],
    ['Websites', pick('shared_website')],
    ['Reused handles', reuse],
  ] as [string, string[]][]).filter(([, v]) => v.length > 0)

  if (rows.length === 0 && entity.platform_links.length === 0) return null

  return (
    <Card>
      <div className="mb-2 text-sm font-semibold">Key identifiers</div>
      {entity.platform_links.length > 0 && (
        <div className={`flex flex-wrap gap-1 ${rows.length ? 'mb-2' : ''}`}>
          {entity.platform_links.map((l, i) => (
            <PlatformBadge
              key={i}
              source={l.source}
              label={`${l.source}:${l.platform_username || l.platform_id}`}
              title={`${l.source} · ${l.is_confirmed ? 'confirmed' : 'candidate'}`}
            />
          ))}
        </div>
      )}
      {rows.map(([label, vals]) => (
        <div key={label} className="mb-1 flex items-baseline gap-2">
          <span className="inline-block min-w-[110px] text-sm text-text-muted">{label}</span>
          <span className="break-all font-mono text-sm">{vals.join('  ·  ')}</span>
        </div>
      ))}
    </Card>
  )
}
