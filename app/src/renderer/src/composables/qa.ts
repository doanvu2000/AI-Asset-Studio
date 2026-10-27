import type { QaResult, VersionRow } from '@/api/types'

export type QaTier = 'good' | 'warn' | 'bad'

export const QA_LOW = 70
/** Must match core `engine/qa.py` QA_VERSION; older results count as not scored. */
export const QA_VERSION = 1

export function qaTier(score: number): QaTier {
  return score >= 85 ? 'good' : score >= QA_LOW ? 'warn' : 'bad'
}

export const QA_TIER_TEXT: Record<QaTier, string> = { good: 'Tốt', warn: 'Tạm được', bad: 'Điểm thấp' }

export function qaScore(qa: QaResult | null | undefined): number | null {
  return typeof qa?.score === 'number' ? qa.score : null
}

/** The lead (sheet / main image) speaks for the asset; otherwise the weakest scored image does. */
export function groupQa(lead: VersionRow, list: VersionRow[]): QaResult | null {
  if (qaScore(lead.qa) !== null) return lead.qa!
  let worst: QaResult | null = null
  for (const v of list) {
    const s = qaScore(v.qa)
    if (s !== null && (worst === null || s < (worst.score as number))) worst = v.qa!
  }
  return worst
}
