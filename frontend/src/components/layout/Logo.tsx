/** Analyzer logo — the collector's funnel mark (many signals funnelled in),
 *  recolored to the analyzer's minimal white/black scheme so the two dashboards
 *  read as a family. */
export function Logo() {
  return (
    <svg viewBox="0 0 48 48" className="h-7 w-7 shrink-0" aria-hidden="true">
      <rect width="48" height="48" rx="11" fill="#111111" stroke="#1e1e1e" />
      <circle cx="13" cy="12" r="2.6" fill="#3b82f6" />
      <circle cx="24" cy="9.5" r="2.6" fill="#a0a0a0" />
      <circle cx="35" cy="12" r="2.6" fill="#3b82f6" />
      <path
        d="M8.5 17 L39.5 17 L27 32 L27 40.5 a1.8 1.8 0 0 1-2.6 1.6 L21 40.2 L21 32 Z"
        fill="#ffffff"
      />
      <circle cx="24" cy="45.4" r="2.2" fill="#3b82f6" />
    </svg>
  )
}
