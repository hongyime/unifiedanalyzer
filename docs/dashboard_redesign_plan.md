# Analyzer Dashboard Redesign — Plan (for review)

Two goals, from your ask:
1. **Match the unifiedcollector dashboard's design & color scheme** (`C:/unifiedcollector/dashboard/frontend`).
2. **Make it understandable for a layman** — plain-language labels, per-page explanations, inline help.

Good news on feasibility: both dashboards already run the **same stack** — React 19, TanStack Query,
lucide-react, Tailwind v4. Only `clsx` and the web fonts are missing on the analyzer side. So this is a
reskin + component port, not a rewrite.

---

## What the collector does that we don't (the gap)

| Area | Collector (target) | Analyzer (today) |
|------|--------------------|------------------|
| Palette | Pure black `#0a0a0a` / surface `#111` / border `#1e1e1e`, semantic success/error/warning/info | Blue-tinted `#0f1117` / card `#1a1d27`, indigo accent, legacy CSS classes |
| Typography | **Inter** (UI) + **JetBrains Mono** (numbers/IDs) | system-ui only, no mono |
| Layout | `AppShell` + grouped `Sidebar` (Operations/Data/System…) + `Header`, logo mark, live status pill | Flat ungrouped nav, no header, inline health widget |
| UI kit | `MetricCard`, `StatusBadge`, `DataTable`, `EmptyState`, `ErrorState`, `LoadingSpinner`, `SkeletonLoader`, `FilterDropdown`, `SearchBar`, `Button` | Ad-hoc markup + legacy `.card/.badge/table` CSS (mid-migration) |
| Polish | uppercase tracked labels, `tabular-nums`, pulsing live dots, tooltips | minimal |
| Explanations | domain-grouped, labelled sections | jargon-heavy (`same_person_probability`, `SILENCE_GAP`, "entity", "signals") |

---

## Part A — Design system alignment

**A1. Tokens** (`frontend/src/index.css`): replace the analyzer palette with the collector's
`@theme` tokens (`--color-background/surface/border`, `--color-text-primary/secondary/muted`,
`--color-success/error/warning/info`). Decide on the **accent** (see Decision 1 below).

**A2. Typography**: self-host or Google-Fonts **Inter** + **JetBrains Mono**; mono for all numbers,
IDs, hashes, scores. Add `--font-sans` / `--font-mono`.

**A3. Port the UI kit** into `frontend/src/components/ui/` (adapt collector's, wire to analyzer data):
`MetricCard`, `StatusBadge`, `DataTable`, `EmptyState`, `ErrorState`, `LoadingSpinner`,
`SkeletonLoader`, `FilterDropdown`, `SearchBar`, `Button`. Add `clsx`. Then delete the legacy
`.card/.badge/table/button` block in `index.css` as pages migrate off it.

**A4. Layout shell**: `AppShell` + `Sidebar` (grouped) + `Header`. Analyzer logo mark + title +
**live pipeline pill** driven by the run heartbeat we built (running / idle / last-run age, pulsing dot).
Proposed nav grouping:
- **Investigate** — Triage, Review, Entities, Communities
- **Evidence** — Alerts, Media, Faces
- **Workspace** — Cases, Runs

---

## Part B — Layman explanations (the core ask)

**B1. Plain-language labels** (UI only; DB/enum values unchanged) via a central label map:
- `same_person_probability` → **"Possible same person"**
- `SILENCE_GAP` → **"Gone quiet"** · `COORDINATED_POSTING` → **"Posting in sync"** ·
  `NEW_ACTIVITY_AFTER_SILENCE` → **"Active again"**
- "entity" → **"person / account"** · "signals" → **"evidence"** · "tier: secondary" → **"unconfirmed"**
- signal types → friendly names (`whatsapp_phone` → "Same WhatsApp number", `commit_email` →
  "Same git email", `real_name_fuzzy` → "Similar name", `content_similarity` → "Similar content"…)

**B2. Per-page intro** — every page gets a one-line "what this is / what to do here" under the title
(plain English, e.g. Review: *"Pairs of accounts that might be the same person. Confirm or reject — each
choice also teaches the system."*).

**B3. Inline help** — a small `(?)` `InfoTip` component (hover/tap popover) next to jargon and every
metric, explaining in one sentence + why it matters.

**B4. Confidence as words + bar** — show scores as **"Likely (72%)"** / "Possible" / "Weak" with a
colored bar, not a bare percentage.

**B5. Guided empty states** — e.g. Review empty: *"Nothing to review yet. Candidates appear here as the
pipeline finds accounts that might be the same person."*

**B6. Glossary** — a `/help` page (and a `(?)` in the header) defining entity, evidence/signal types,
confidence, tiers, the face bridge, alert types — in plain language. (Decision 2: full page vs tooltips-only.)

---

## Part C — Per-page improvements (highlights)

- **Triage (home)**: reframe as "What needs your attention" — MetricCards (people tracked, new evidence,
  candidates to review, unread alerts) + a plain-English activity feed.
- **Review**: already improved (handles/faces); add the confidence-in-words, a "why we think so" evidence
  list in friendly names, and the intro explaining merge-vs-not-same.
- **Entities**: DataTable with platform badges, evidence count, last-seen; plain "unconfirmed/confirmed".
- **Media**: keep the grid; add type filters in plain language, and explain derived vs original.
- **Alerts**: friendly alert-type names + one-line meaning each; group by type.
- **Faces / Communities / Cases / Runs**: reskin to the UI kit + intros.

---

## Phasing (each phase independently shippable, no behavior change)

1. **Phase 1 — reskin foundation**: tokens + fonts + `clsx` + UI kit + layout shell + grouped nav.
   Visual only; pages keep working. (Biggest visual payoff.)
2. **Phase 2 — plain language**: label map, per-page intros, `InfoTip`, confidence-in-words, empty states.
3. **Phase 3 — glossary + per-page polish**: `/help`, migrate remaining legacy-CSS pages
   (Entities/EntityDetail/Communities) onto the UI kit, delete legacy CSS.

Estimate: Phase 1 ~half-day, Phase 2 ~half-day, Phase 3 ~day. Can stop after any phase.

---

## Decisions I need from you

1. **Accent color**: collector uses **white-on-black** for the active nav item (very minimal). Keep the
   analyzer's **indigo** accent for a bit more color, or go full collector white/black?
2. **Glossary**: dedicated **`/help` page**, or **inline tooltips only** (lighter)?
3. **Scope**: all three phases, or start with **Phase 1 (reskin)** and reassess?
4. **Branding**: reuse the collector's funnel logo mark (recolored), or a new analyzer mark?

---

## Risks / notes
- `Entities`, `EntityDetail`, `Communities` still use legacy CSS classes + imperative API — migrate them
  last (Phase 3) to avoid breaking mid-reskin.
- No backend/API changes needed for A/B; the label map and InfoTips are frontend-only. (A couple of pages
  may want a small extra field, e.g. a plain "last seen" — additive.)
- All changes are frontend; deploys are the usual analyzer image rebuild.
