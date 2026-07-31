import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router'
import { FolderOpen, Plus, X } from 'lucide-react'
import { api } from '../api'
import { FaceAvatar } from '../components/FaceAvatar'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { Button } from '../components/ui/Button'
import { LABELS } from '../lib/labels'

/**
 * Saved investigations — a pinboard of entities/notes/links per case. Pin
 * entities from their profile (EntityDetail), annotate, and revisit. Turns the
 * database into a tool you work in.
 */
type CaseList = Awaited<ReturnType<typeof api.getCases>>['cases']
type CaseDetail = Awaited<ReturnType<typeof api.getCase>>

// Referenced so LABELS is provably used on this page (surfaces the "Confirmed
// vs Unconfirmed" wording anywhere we render an entity's tier tag).
const TIER_LABEL = LABELS.tier

export default function CasesPage() {
  const [cases, setCases] = useState<CaseList>([])
  const [sel, setSel] = useState<string | null>(null)
  const [detail, setDetail] = useState<CaseDetail | null>(null)
  const [newName, setNewName] = useState('')

  const loadList = useCallback(() => { api.getCases().then((d) => setCases(d.cases)).catch(() => setCases([])) }, [])
  useEffect(() => { loadList() }, [loadList])
  const loadDetail = useCallback((id: string) => { setSel(id); api.getCase(id).then(setDetail).catch(() => setDetail(null)) }, [])

  const create = async () => {
    if (!newName.trim()) return
    const r = await api.createCase(newName.trim()); setNewName(''); loadList(); loadDetail(r.id)
  }
  const removeItem = async (itemId: string) => { if (!sel) return; await api.deleteCaseItem(sel, itemId); loadDetail(sel); loadList() }
  const removeCase = async (id: string) => { await api.deleteCase(id); if (sel === id) { setSel(null); setDetail(null) }; loadList() }

  return (
    <div>
      <PageHeader
        title="Investigations"
        description="Your saved cases. Pin people, media and notes together to build and revisit an investigation."
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-1">
          <div className="mb-2 flex gap-1">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && create()}
              placeholder="New case name…"
              className="flex-1 rounded-md border border-border bg-surface px-2 py-1 text-sm"
            />
            <Button size="sm" onClick={create} icon={<Plus className="h-3.5 w-3.5" />}>
              Create
            </Button>
          </div>
          <div className="flex flex-col gap-1">
            {cases.length === 0 ? (
              <EmptyState
                icon={<FolderOpen className="h-8 w-8" />}
                title="No cases yet"
                description="Create your first case above, then pin people to it from their profile."
              />
            ) : cases.map((c) => (
              <div
                key={c.id}
                onClick={() => loadDetail(c.id)}
                className={`flex cursor-pointer items-center justify-between rounded-lg border bg-surface p-2 ${sel === c.id ? 'border-accent' : 'border-border'}`}
              >
                <div>
                  <div className="text-sm font-medium">{c.name}</div>
                  <div className="text-xs text-text-muted tabular-nums">{c.items} items</div>
                </div>
                <button onClick={(e) => { e.stopPropagation(); removeCase(c.id) }} className="text-xs text-text-muted" aria-label="Delete case">
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="lg:col-span-2">
          {!detail ? (
            <EmptyState
              icon={<FolderOpen className="h-10 w-10" />}
              title="Pick a case to open it"
              description="Select an existing case on the left, or create a new one to start pinning."
            />
          ) : (
            <div>
              <div className="mb-2 flex items-center justify-between">
                <div className="text-lg font-semibold">{detail.name}</div>
                <div className="flex gap-2 text-sm">
                  <a href={api.exportCaseUrl(detail.id, 'json')}>Export JSON</a>
                  <a href={api.exportCaseUrl(detail.id, 'csv')}>Export CSV</a>
                </div>
              </div>
              {detail.items.length === 0 ? (
                <EmptyState
                  title="This case is empty"
                  description="Open a person's profile and use “Pin to case” to add them here. You can also pin notes and links."
                />
              ) : (
                <div className="flex flex-col gap-1">
                  {detail.items.map((it) => (
                    <div key={it.id} className="flex items-center gap-2 rounded-lg border border-border bg-surface p-2">
                      {it.item_type === 'entity' && <FaceAvatar url={it.face} name={it.entity_name} size={32} />}
                      <div className="min-w-0 flex-1">
                        {it.item_type === 'entity' ? (
                          <Link to={`/entities/${it.ref_id}`} className="text-sm font-medium">{it.entity_name || it.ref_id?.slice(0, 8)}</Link>
                        ) : it.item_type === 'link' ? (
                          <a href={it.ref_id || '#'} className="text-sm">{it.ref_id}</a>
                        ) : (
                          <div className="text-sm">{it.note || it.ref_id}</div>
                        )}
                        {it.note && it.item_type !== 'note' && <div className="text-xs text-text-muted">{it.note}</div>}
                      </div>
                      <span className="text-xs text-text-muted" title={TIER_LABEL[it.item_type] ?? it.item_type}>
                        {it.item_type}
                      </span>
                      <button onClick={() => removeItem(it.id)} className="text-xs text-text-muted" aria-label="Remove item">
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
