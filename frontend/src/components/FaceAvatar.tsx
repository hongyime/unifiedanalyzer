import type { CSSProperties } from 'react'

/**
 * Face thumbnail for a person. Shows the entity's representative face crop
 * (served by /api/face/gallery) and falls back to the name initial when there's
 * no bridged face yet or the source media isn't reachable. Uses inline styles +
 * CSS-var fallbacks so it renders consistently across the app's two style
 * conventions.
 */
export function FaceAvatar({
  url,
  name,
  size = 32,
}: {
  url?: string | null
  name?: string | null
  size?: number
}) {
  const initial = (name?.trim()?.[0] || '?').toUpperCase()
  const box: CSSProperties = {
    width: size,
    height: size,
    minWidth: size,
    borderRadius: '50%',
    overflow: 'hidden',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'var(--card, #17171c)',
    border: '1px solid var(--border, #2a2a33)',
    fontSize: Math.round(size * 0.4),
    fontWeight: 600,
    color: 'var(--muted, #8a8a99)',
    flexShrink: 0,
  }
  return (
    <span style={box} title={name || undefined}>
      {url ? (
        <img
          src={url}
          alt={name || 'face'}
          loading="lazy"
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          onError={(e) => {
            const im = e.currentTarget
            im.style.display = 'none'
            if (im.parentElement) im.parentElement.textContent = initial
          }}
        />
      ) : (
        initial
      )}
    </span>
  )
}
