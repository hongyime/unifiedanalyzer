import { FormEvent, useState } from 'react'
import { Search } from 'lucide-react'
import { api, TimelineSearchResponse } from '../api'
import { PageHeader } from '../components/ui/PageHeader'
import { Button } from '../components/ui/Button'
import { EmptyState } from '../components/ui/EmptyState'

type Mode = 'hybrid' | 'keyword' | 'semantic'

export default function SearchPage() {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<Mode>('hybrid')
  const [result, setResult] = useState<TimelineSearchResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!query.trim()) return
    setLoading(true)
    setError('')
    api.searchTimeline(query, mode, 50)
      .then(setResult)
      .catch((e) => setError(e.message || 'Search failed'))
      .finally(() => setLoading(false))
  }

  return (
    <div>
      <PageHeader title="Search" description="Search timeline evidence with exact text, semantic matches, or hybrid ranking." />
      <form onSubmit={submit} className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[260px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="w-full rounded-md border border-border bg-surface py-2 pl-9 pr-3 text-sm outline-none focus:border-accent"
            placeholder="Search messages, handles, URLs, topics..."
          />
        </div>
        <div className="inline-flex rounded-md border border-border bg-surface p-0.5">
          {(['hybrid', 'keyword', 'semantic'] as Mode[]).map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setMode(item)}
              className={`rounded px-3 py-1.5 text-sm ${mode === item ? 'bg-white text-black' : 'text-text-secondary hover:bg-white/5'}`}
            >
              {item}
            </button>
          ))}
        </div>
        <Button loading={loading} disabled={loading || !query.trim()} icon={<Search className="h-3.5 w-3.5" />}>
          Search
        </Button>
      </form>
      {error && <div className="mb-3 rounded-md border border-error/40 bg-error/10 p-3 text-sm text-error">{error}</div>}
      {!result ? (
        <EmptyState title="No search yet" description="Run a timeline search to inspect sparse, dense, and fused ranks." />
      ) : result.results.length === 0 ? (
        <EmptyState title="No matches" description="Try hybrid mode for recall or keyword mode for exact handles and URLs." />
      ) : (
        <div className="space-y-2">
          <div className="text-sm text-text-muted">
            {result.results.length} results · {result.took_ms} ms · {result.mode}
          </div>
          {result.results.map((row) => (
            <a
              key={row.event_id}
              href={row.entity_id ? `/entities/${row.entity_id}` : '#'}
              className="block rounded-lg border border-border bg-surface p-3 hover:bg-hover"
            >
              <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-text-muted">
                <span>{row.platform}</span>
                <span>{row.occurred_at ? new Date(row.occurred_at).toLocaleString() : '-'}</span>
                <span>score {row.score.toFixed(4)}</span>
                {row.keyword_rank && <span>sparse #{row.keyword_rank}</span>}
                {row.semantic_rank && <span>dense #{row.semantic_rank}</span>}
                {row.rrf_rank && <span>rrf #{row.rrf_rank}</span>}
                {row.match_debug?.matched_translation && (
                  <span className="rounded-full bg-info/15 px-2 py-0.5 text-info">translated match</span>
                )}
              </div>
              <div className="line-clamp-3 text-sm text-text-primary">{row.snippet || row.event_id}</div>
            </a>
          ))}
        </div>
      )}
    </div>
  )
}
