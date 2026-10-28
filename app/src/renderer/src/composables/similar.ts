import type { DuplicateKind, MoreLikePlan, SimilarMode } from '@/api/types'
import { PREFILL_PREFIX } from './followUp'

/** `/new` query for "Tạo thêm giống cái này": the plan's recipe with every input prefilled (read back by NewRunView). */
export function moreLikeQuery(plan: MoreLikePlan, fallbackProject?: string): Record<string, string | string[] | undefined> {
  const project = plan.project_id ?? fallbackProject
  const q: Record<string, string | string[] | undefined> = { recipe: plan.recipe_id, project: project && project !== 'default' ? project : undefined }
  for (const [name, value] of Object.entries(plan.inputs)) {
    if (value === null || value === undefined || value === '') continue
    if (Array.isArray(value)) q[`${PREFILL_PREFIX}${name}`] = value.map(String)
    else q[`${PREFILL_PREFIX}${name}`] = typeof value === 'object' ? JSON.stringify(value) : String(value)
  }
  return q
}

export const MORE_LIKE_HINT: Record<MoreLikePlan['mode'], string> = {
  style_ref: 'cùng quy trình, ảnh này làm style mẫu',
  style_recipe: 'quy trình theo style tham chiếu, giữ prompt',
  same_recipe: 'chạy lại cùng quy trình và đầu vào'
}

export const DUP_LABEL: Record<DuplicateKind, string> = { exact: 'Trùng hệt', near: 'Gần như trùng', similar: 'Rất giống' }

export function scoreText(score: number, mode: SimilarMode): string {
  return mode === 'clip' ? `${Math.round(score * 100)}%` : `~${Math.round(score * 100)}%`
}
