import { clsx } from '../../lib/cx'

interface FilterDropdownProps {
  label: string
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
  className?: string
}

/** Small labelled select used to build filter bars. Uses the collector's
 *  black-on-black look so filter chrome disappears until interacted with. */
export function FilterDropdown({ label, value, onChange, options, className }: FilterDropdownProps) {
  return (
    <label className={clsx('flex items-center gap-2', className)}>
      <span className="text-xs text-text-muted">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-border bg-background px-2 py-1.5 text-sm text-text-primary
                   focus:outline-none focus:ring-2 focus:ring-white/20"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}
