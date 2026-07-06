/**
 * One-liner intro under a page title. Every migrated page carries one so the
 * dashboard is self-explanatory: "what this page is / what to do here".
 *
 * `PageHeader` composes this internally via its `description` prop — pages
 * using PageHeader get PageIntro semantics for free.
 */
export function PageIntro({ text, className }: { text: string; className?: string }) {
  return (
    <p className={`max-w-2xl text-sm text-text-secondary ${className ?? ''}`}>{text}</p>
  )
}
