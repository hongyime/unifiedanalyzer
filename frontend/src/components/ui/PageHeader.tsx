import type { ReactNode } from 'react'
import { PageIntro } from './PageIntro'

interface PageHeaderProps {
  title: string
  /** Plain-language, one-line "what this page is for / what to do here". */
  description?: string
  actions?: ReactNode
}

/** Standard page top: title + `<PageIntro>` + optional right-aligned actions. */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <div className="mb-6 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {description && <PageIntro text={description} className="mt-1" />}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}
