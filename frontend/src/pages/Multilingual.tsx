import { useEffect, useState } from 'react'
import { Languages, RefreshCw } from 'lucide-react'
import { api, MultilingualStatus } from '../api'
import { PageHeader } from '../components/ui/PageHeader'
import { MetricCard } from '../components/ui/MetricCard'
import { Card } from '../components/ui/Card'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { ErrorState } from '../components/ui/ErrorState'
import { EmptyState } from '../components/ui/EmptyState'
import { StatusBadge } from '../components/ui/StatusBadge'

function pct(value: number | undefined) {
  return `${Number(value ?? 0).toFixed(1)}%`
}

function text(value: unknown, fallback = '-') {
  return typeof value === 'string' && value ? value : fallback
}

function boolText(value: unknown) {
  return value ? 'on' : 'off'
}

export default function MultilingualPage() {
  const [status, setStatus] = useState<MultilingualStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    api.getMultilingualStatus()
      .then(setStatus)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  if (loading) return <LoadingSpinner label="Loading multilingual coverage..." />
  if (error) return <ErrorState message={`Failed to load multilingual status: ${error}`} onRetry={load} />
  if (!status) return <EmptyState title="No multilingual status" description="Language-profile and translation counts are unavailable." />

  const translationIssues = status.failed_translation_rows + status.skipped_translation_rows + status.unsupported_rows
  const detector = status.language_detector ?? {}
  const worker = status.translation_worker ?? {}

  return (
    <div>
      <PageHeader
        title="Multilingual NLP"
        description="Language detection and bounded translation coverage for timeline text. Machine translations are context only."
        actions={<button type="button" onClick={load}><RefreshCw className="mr-1 inline h-3.5 w-3.5" />Refresh</button>}
      />

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-6">
        <MetricCard label="Text rows" value={status.text_rows} icon={<Languages className="h-4 w-4" />} />
        <MetricCard label="Profiled" value={status.profile_rows} sublabel={pct(status.profile_coverage_pct)} status={status.profile_rows ? 'success' : 'idle'} />
        <MetricCard label="Translated" value={status.translated_rows} sublabel={pct(status.translation_coverage_pct)} />
        <MetricCard label="Code-mixed" value={status.code_mixed_rows} />
        <MetricCard label="Unsupported" value={status.unsupported_rows} status={status.unsupported_rows ? 'warning' : 'success'} />
        <MetricCard label="Failures" value={status.failed_translation_rows} status={status.failed_translation_rows ? 'error' : 'success'} />
      </div>

      <div className="mb-6 grid gap-3 md:grid-cols-3">
        <Card>
          <div className="mb-2 flex items-center justify-between">
            <div className="text-sm font-semibold">Pipeline state</div>
            <StatusBadge status={translationIssues ? 'warning' : 'success'} label={translationIssues ? 'needs review' : 'clear'} />
          </div>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between gap-3"><span className="text-text-muted">Language profiles</span><span>{status.profile_rows.toLocaleString()}</span></div>
            <div className="flex justify-between gap-3"><span className="text-text-muted">Translation rows</span><span>{status.translation_rows.toLocaleString()}</span></div>
            <div className="flex justify-between gap-3"><span className="text-text-muted">Skipped translations</span><span>{status.skipped_translation_rows.toLocaleString()}</span></div>
          </div>
        </Card>
        <Card className="md:col-span-2">
          <div className="mb-2 text-sm font-semibold">Runtime</div>
          <div className="grid gap-2 text-sm sm:grid-cols-2">
            <div className="rounded-md bg-background p-2">
              <div className="text-xs text-text-muted">Language detector</div>
              <div className="font-medium">
                fastText {boolText(detector.fasttext_loaded)} · fallback {boolText(detector.fallback_detector)}
              </div>
              <div className="text-xs text-text-muted">{text(detector.fasttext_model_path, 'no model path')}</div>
            </div>
            <div className="rounded-md bg-background p-2">
              <div className="text-xs text-text-muted">Translation worker</div>
              <div className="font-medium">
                {text(worker.provider, 'noop')} · {Number(worker.max_per_run ?? 0).toLocaleString()} per run
              </div>
              <div className="text-xs text-text-muted">
                OPUS {boolText(worker.opus_mt_enabled)} · NLLB {boolText(worker.nllb_enabled)}
              </div>
            </div>
          </div>
        </Card>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <div className="mb-3 text-sm font-semibold">Languages detected</div>
          {status.languages.length === 0 ? (
            <div className="text-sm text-text-muted">No language profiles yet.</div>
          ) : (
            <div className="overflow-x-auto">
              <table>
                <thead>
                  <tr><th>Language</th><th>Rows</th><th>Share</th></tr>
                </thead>
                <tbody>
                  {status.languages.map((row) => (
                    <tr key={row.language}>
                      <td className="font-medium">{row.language}</td>
                      <td>{row.count.toLocaleString()}</td>
                      <td>{pct(status.profile_rows ? (row.count / status.profile_rows) * 100 : 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card>
          <div className="mb-3 text-sm font-semibold">Translation failures and skips</div>
          {status.failures.length === 0 ? (
            <div className="text-sm text-text-muted">No failure reasons recorded.</div>
          ) : (
            <div className="overflow-x-auto">
              <table>
                <thead>
                  <tr><th>Reason</th><th>Rows</th></tr>
                </thead>
                <tbody>
                  {status.failures.map((row) => (
                    <tr key={row.reason}>
                      <td className="break-all">{row.reason}</td>
                      <td>{row.count.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
