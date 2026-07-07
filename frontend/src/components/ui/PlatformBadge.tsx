/**
 * Small coloured chip for a platform source (github / instagram / …).
 * Uses the `.platform-icon` + per-platform `.p-*` CSS utilities in index.css so
 * the same visual scheme is shared across every page. Rendered as an inline
 * badge — nothing more.
 */
export function PlatformBadge({ source, label, title }: { source: string; label?: string; title?: string }) {
  return (
    <span className={`platform-icon p-${source}`} title={title}>
      {label ?? source}
    </span>
  )
}
