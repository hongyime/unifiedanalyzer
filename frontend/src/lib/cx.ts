/** Tiny classnames helper (clsx-compatible for our string/falsy usage) — avoids
 *  adding a dependency + lockfile churn. */
export function clsx(...args: (string | false | null | undefined)[]): string {
  return args.filter(Boolean).join(' ')
}
