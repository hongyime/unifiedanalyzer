/**
 * Plain-language layer: turns internal jargon (signal types, alert types, tiers,
 * scores) into words a non-technical reader understands. UI-only — the underlying
 * DB/enum values are unchanged. Also the single source of truth for the glossary
 * (Help page) and the (?) InfoTips.
 *
 * PUBLIC API
 * ─────────
 * • `LABELS.signalType[key]`  — friendly name for an identity-signal type
 * • `LABELS.alertType[key]`   — friendly name for an alert type (just the name)
 * • `LABELS.tier[key]`        — 'Confirmed' / 'Unconfirmed' for entity tier
 * • `LABELS.confidence(score)`— {word, pct, tone} for a 0..1 same-person score
 *
 * The legacy helper exports (`signalLabel`, `alertLabel`, `alertMeaning`,
 * `tierLabel`, `confidenceWords`) are preserved so existing pages keep working
 * while the new `LABELS.*` shape is adopted incrementally.
 */

// ── Signals ("evidence") ────────────────────────────────────────────────────
export const SIGNAL_LABELS: Record<string, string> = {
  // The composite score itself (surfaces in metrics + tooltips, not a raw signal).
  same_person_probability: 'Possible same person',

  // Contact-level matches.
  phone_match: 'Same phone number',
  email_match: 'Same email',
  whatsapp_phone: 'Same WhatsApp number',
  commit_email: 'Same git email',

  // Name / handle.
  real_name_fuzzy: 'Similar name',
  username_exact: 'Same username',
  username_similar: 'Similar username',

  // Cross-platform links.
  cross_platform_link: 'Linked across platforms',
  shared_website: 'Same personal website',
  bio_mention: 'Mentions each other in bio',

  // Content / behaviour.
  content_similarity: 'Similar writing style',
  topical_similarity: 'Talks about similar topics',
  temporal_copost: 'Posts at the same times',
  group_cooccurrence: 'Members of the same groups',
  shared_route_origin: 'Starts activities from the same place',

  // Media-derived.
  profile_photo_sha256: 'Same profile photo',
  media_face_match: 'Same face in photos',
  media_perceptual_match: 'Same photo posted',
  media_gps_colocation: 'Photos taken at the same place',
  media_device_match: 'Same camera device',
  face_pair_knn: 'Same face across accounts',
}

/** Friendly name for an identity-signal type ("evidence"). Falls back to a
 *  cleaned-up version of the raw key if unknown. */
export function signalLabel(type: string): string {
  return SIGNAL_LABELS[type] ?? type.replace(/_/g, ' ')
}

// ── Alerts ──────────────────────────────────────────────────────────────────
export const ALERT_LABELS: Record<string, { name: string; meaning: string }> = {
  SILENCE_GAP: {
    name: 'Gone quiet',
    meaning: 'This person stopped posting for longer than usual.',
  },
  NEW_ACTIVITY_AFTER_SILENCE: {
    name: 'Active again',
    meaning: 'Posting resumed after a long quiet period.',
  },
  COORDINATED_POSTING: {
    name: 'Posting in sync',
    meaning: 'Two accounts repeatedly post within minutes of each other across platforms.',
  },
  NEW_IDENTITY_LINK: {
    name: 'Possible same person found',
    meaning: 'The system found new evidence that two accounts may be the same person.',
  },
  PROFILE_CHANGE: {
    name: 'Profile changed',
    meaning: 'A username or display name changed.',
  },
  LOCATION_MISMATCH: {
    name: 'Location conflict',
    meaning: 'Accounts for this person show up in different countries.',
  },
  IDENTITY_BREACHED: {
    name: 'Data breach exposure',
    meaning: 'This person\u2019s email appears in one or more known data breaches.',
  },
  CALIBRATION_READY: {
    name: 'Calibration cutover ready',
    meaning: 'The trained same-person model now beats hand-set weights. Ready to activate.',
  },
}

export function alertLabel(type: string): string {
  return ALERT_LABELS[type]?.name ?? type.replace(/_/g, ' ')
}
export function alertMeaning(type: string): string | undefined {
  return ALERT_LABELS[type]?.meaning
}

// ── Tiers ───────────────────────────────────────────────────────────────────
const TIER_LABELS: Record<string, string> = {
  primary: 'Confirmed',
  secondary: 'Unconfirmed',
}

/** Entity tier in plain words. */
export function tierLabel(tier: string | null | undefined): string {
  if (!tier) return ''
  return TIER_LABELS[tier] ?? tier
}

// ── Confidence ──────────────────────────────────────────────────────────────
export type ConfidenceTone = 'success' | 'warning' | 'muted' | 'error'

export interface ConfidenceReading {
  word: string
  pct: number
  tone: ConfidenceTone
}

/**
 * A same-person score (0–1) as a word + the raw %.
 *
 * Bands (from plan doc):
 *   <0.30           → Weak         (muted)
 *   0.30 – 0.60     → Possible     (warning)
 *   0.60 – 0.85     → Likely       (success)
 *   >= 0.85         → Very likely  (success)
 *
 * The `ConfidencePill` colour bar maps to (red<0.30, amber<0.60, green>=0.60).
 */
export function confidenceWords(score: number | null | undefined): ConfidenceReading {
  const pct = Math.round((score ?? 0) * 100)
  if (pct >= 85) return { word: 'Very likely', pct, tone: 'success' }
  if (pct >= 60) return { word: 'Likely', pct, tone: 'success' }
  if (pct >= 30) return { word: 'Possible', pct, tone: 'warning' }
  return { word: 'Weak', pct, tone: 'muted' }
}

// ── Canonical exported map ──────────────────────────────────────────────────
/**
 * The single object every migrated page should reach for. Structured so a
 * page can do `LABELS.signalType[key]` / `LABELS.alertType[key]` /
 * `LABELS.tier[key]` / `LABELS.confidence(score)` — no imports of the
 * individual helpers required.
 */
export const LABELS = {
  signalType: SIGNAL_LABELS,
  alertType: Object.fromEntries(
    Object.entries(ALERT_LABELS).map(([k, v]) => [k, v.name]),
  ) as Record<string, string>,
  tier: TIER_LABELS,
  confidence: confidenceWords,
}

// ── Glossary (Help page + InfoTips) ─────────────────────────────────────────
export const GLOSSARY: { term: string; def: string }[] = [
  {
    term: 'Person / account (entity)',
    def: 'A single person the system is tracking, built from one or more social/platform accounts that appear to belong to them.',
  },
  {
    term: 'Evidence (signal)',
    def: 'A single clue that two accounts belong to the same person — e.g. the same phone number, a similar name, or the same face in photos. More independent clues = higher confidence.',
  },
  {
    term: 'Confirmed vs Unconfirmed',
    def: 'Confirmed people have strong, multiple pieces of evidence. Unconfirmed ones (often lone WhatsApp numbers) are kept but flagged as lower-certainty.',
  },
  {
    term: 'Possible same person',
    def: 'A pair of accounts the system thinks might be one person. You confirm or reject these in Review — each decision also trains the system.',
  },
  {
    term: 'Confidence / score',
    def: 'How sure the system is, shown as Very likely / Likely / Possible / Weak with a percentage. It combines all the evidence for a pair.',
  },
  {
    term: 'Face bridge',
    def: 'When the same face appears across different photos/accounts, those accounts get linked. Only faces we can attribute to a tracked person count.',
  },
  {
    term: 'Gone quiet / Active again',
    def: 'Alerts about someone’s posting rhythm — they stopped for longer than their normal gap, or started again after a long pause.',
  },
  {
    term: 'Posting in sync',
    def: 'Two accounts that repeatedly post within minutes of each other across different platforms — a hint they’re run by the same person.',
  },
  {
    term: 'Run (incremental / full)',
    def: 'The background pipeline that refreshes everything. Incremental runs are frequent and quick; full runs re-check everyone from scratch.',
  },
]
