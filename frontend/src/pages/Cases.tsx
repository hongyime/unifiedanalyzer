import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { FaceAvatar } from '../components/FaceAvatar'

/**
 * Saved investigations — a pinboard of entities/notes/links per case. Pin
 * entities from their profile (EntityDetail), annotate, and revisit. Turns the
 * database into a tool you work in.
 */
type CaseList = Awaited<ReturnType<typeof api.getCases>>['cases']
type CaseDetail = Awaited<ReturnType<typeof api.getCase>>

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
      <h2 className="mb-1 text-xl font-bold">Investigations</h2>
      <p className="mb-4 text-sm text-muted">Saved cases — pin entities/notes, annotate, revisit.</p>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-1">
          <div className="mb-2 flex gap-1">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && create()}
              placeholder="New case name…"
              className="flex-1 rounded-md border border-border bg-card px-2 py-1 text-sm"
            />
            <button onClick={create}>Create</button>
          </div>
          <div className="flex flex-col gap-1">
            {cases.length === 0 ? (
              <div className="empty-state">No cases yet</div>
            ) : cases.map((c) => (
              <div
                key={c.id}
                onClick={() => loadDetail(c.id)}
                className={`flex cursor-pointer items-center justify-between rounded-lg border bg-card p-2 ${sel === c.id ? 'border-accent' : 'border-border'}`}
              >
                <div>
                  <div className="text-sm font-medium">{c.name}</div>
                  <div className="text-xs text-muted">{c.items} items</div>
                </div>
                <button onClick={(e) => { e.stopPropagation(); removeCase(c.id) }} className="text-xs text-muted">✕</button>
              </div>
            ))}
          </div>
        </div>

        <div className="lg:col-span-2">
          {!detail ? (
            <div className="empty-state">Select or create a case</div>
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
                <div className="empty-state">Empty — pin entities from their profile (Pin to case)</div>
              ) : (
                <div className="flex flex-col gap-1">
                  {detail.items.map((it) => (
                    <div key={it.id} className="flex items-center gap-2 rounded-lg border border-border bg-card p-2">
                      {it.item_type === 'entity' && <FaceAvatar url={it.face} name={it.entity_name} size={32} />}
                      <div className="min-w-0 flex-1">
                        {it.item_type === 'entity' ? (
                          <Link to={`/entities/${it.ref_id}`} className="text-sm font-medium">{it.entity_name || it.ref_id?.slice(0, 8)}</Link>
                        ) : it.item_type === 'link' ? (
                          <a href={it.ref_id || '#'} className="text-sm">{it.ref_id}</a>
                        ) : (
                          <div className="text-sm">{it.note || it.ref_id}</div>
                        )}
                        {it.note && it.item_type !== 'note' && <div className="text-xs text-muted">{it.note}</div>}
                      </div>
                      <span className="text-xs text-muted">{it.item_type}</span>
                      <button onClick={() => removeItem(it.id)} className="text-xs text-muted">✕</button>
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
