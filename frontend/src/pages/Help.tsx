import { PageHeader } from '../components/ui/PageHeader'
import { GLOSSARY, SIGNAL_LABELS, ALERT_LABELS } from '../lib/labels'

/** Plain-language glossary + a "what each page does" guide, so a non-technical
 *  reader can make sense of the whole dashboard. */
export default function HelpPage() {
  return (
    <div>
      <PageHeader
        title="Help & Glossary"
        description="Plain-language explanations of everything in this dashboard. No jargon required."
      />

      <section className="mb-8">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-text-muted">
          The basics
        </h2>
        <div className="grid gap-3 sm:grid-cols-2">
          {GLOSSARY.map((g) => (
            <div key={g.term} className="rounded-lg border border-border bg-surface p-4">
              <div className="mb-1 font-medium">{g.term}</div>
              <div className="text-sm text-text-secondary">{g.def}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="mb-8">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-text-muted">
          Types of evidence
        </h2>
        <p className="mb-3 max-w-2xl text-sm text-text-secondary">
          Each clue linking two accounts to the same person. The more independent kinds of
          evidence a pair shares, the more confident the system is.
        </p>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(SIGNAL_LABELS).map(([k, v]) => (
            <div key={k} className="flex items-center justify-between rounded border border-border bg-surface px-3 py-2 text-sm">
              <span>{v}</span>
              <code className="font-mono text-[0.65rem] text-text-muted">{k}</code>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-text-muted">
          Alert types
        </h2>
        <div className="grid gap-3 sm:grid-cols-2">
          {Object.entries(ALERT_LABELS).map(([k, v]) => (
            <div key={k} className="rounded-lg border border-border bg-surface p-4">
              <div className="mb-1 font-medium">{v.name}</div>
              <div className="text-sm text-text-secondary">{v.meaning}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
