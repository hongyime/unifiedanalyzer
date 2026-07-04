import type { ReactNode } from 'react'

interface PageHeaderProps {
  title: string
  /** Plain-language, one-line "what this page is for / what to do here". */
  description?: string
  actions?: ReactNode
}

/** Standard page top: title + a plain-English intro so every page explains itself. */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <div className="mb-6 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-sm text-text-secondary">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}
