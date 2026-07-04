import { HelpCircle } from 'lucide-react'

/**
 * A small (?) help affordance. Hovering (or focusing) reveals a plain-language
 * explanation — used next to jargon and metrics throughout the dashboard so a
 * non-technical reader is never left guessing what a term means.
 */
export function InfoTip({ text }: { text: string }) {
  return (
    <span className="group relative inline-flex align-middle">
      <button
        type="button"
        tabIndex={0}
        aria-label={text}
        className="!p-0 !border-0 !bg-transparent text-text-muted hover:text-text-primary cursor-help"
      >
        <HelpCircle className="w-3.5 h-3.5" />
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute left-1/2 top-full z-50 mt-1 w-56 -translate-x-1/2
                   rounded-md border border-border bg-surface p-2 text-left text-xs font-normal
                   normal-case tracking-normal text-text-secondary opacity-0 shadow-lg
                   transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
      >
        {text}
      </span>
    </span>
  )
}
