import { EntityDetail } from '../api'

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
    <div className="card" style={{ marginBottom: '0.75rem' }}>
      <div className="text-sm mb-2" style={{ fontWeight: 600 }}>Key identifiers</div>
      {entity.platform_links.length > 0 && (
        <div className="flex gap-1" style={{ flexWrap: 'wrap', marginBottom: rows.length ? '0.6rem' : 0 }}>
          {entity.platform_links.map((l, i) => (
            <span key={i} className={`platform-icon p-${l.source}`} title={`${l.source} · ${l.is_confirmed ? 'confirmed' : 'candidate'}`}>
              {l.source}:{l.platform_username || l.platform_id}
            </span>
          ))}
        </div>
      )}
      {rows.map(([label, vals]) => (
        <div key={label} className="flex gap-1" style={{ alignItems: 'baseline', marginBottom: '0.25rem' }}>
          <span className="text-sm text-muted" style={{ minWidth: 110, display: 'inline-block' }}>{label}</span>
          <span className="text-sm" style={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>{vals.join('  ·  ')}</span>
        </div>
      ))}
    </div>
  )
}
