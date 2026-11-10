import { z } from 'zod'

// 진단 비교(GET /api/portfolio/diagnosis/{id}/compare?view=record)에 더해진 필드들
// (docs/FRONTEND_HANDOFF_I18N_HISTORY.md 3-6절, FRONTEND_HANDOFF_ETF_EXPOSURE.md 6-2절).
// 기존 비교 응답(current/previous/changes/disclaimer)은 그대로고, view=record면 아래가 더 온다.

export interface DiagnosisCompareBasis {
  current_created_at: string
  current_generated_at: string | null
  previous_created_at: string | null
  previous_generated_at: string | null
  // 항상 false: 두 진단은 각각 저장 당시의 보유·시세로 계산한 값이고, 현재 시세로 다시 계산하지 않는다
  recomputed: boolean
  // 비교 화면 맨 위에 항상 보여준다("기준 시점이 서로 다르고…")
  note: string | null
}

export interface MetricChange {
  previous: number | null
  current: number | null
  change: number | null
}

export interface DiagnosisMetricChanges {
  cash_balance: MetricChange
  cash_weight_pct: MetricChange
  stock_eval_amount: MetricChange
  holding_count: MetricChange
  top1_weight_pct: MetricChange
  top3_weight_pct: MetricChange
  herfindahl_index: MetricChange
  total_profit_loss_pct: MetricChange
}

export interface ExposureCompanyChange {
  name: string
  code: string | null
  previous_total_weight_pct: number | null
  current_total_weight_pct: number | null
  change_pct: number | null
  previous_total_amount: number | null
  current_total_amount: number | null
  status: 'added' | 'removed' | 'changed'
}

// 두 진단 저장본 모두에 실질 노출 스냅샷(status ok)이 있을 때만 온다. 이 기능(2026-11-06) 전에 저장한 진단이
// 한쪽이라도 끼면 null이다. 비교 대상 기업은 각 저장본에 남긴 상위 20개끼리다(companies_compared: top_saved).
export interface ExposureChanges {
  calc_version: { previous: string | null; current: string | null }
  valuation_date: { previous: string | null; current: string | null }
  summary: Record<string, MetricChange>
  look_through_top1: unknown
  companies: ExposureCompanyChange[]
  note_code: string | null
  companies_compared: string | null
}

const nn = z.number().nullish().transform((v) => v ?? null)
const ns = z.string().nullish().transform((v) => v ?? null)

const metricChangeSchema = z.object({ previous: nn, current: nn, change: nn }) satisfies z.ZodType<MetricChange, unknown>

export const diagnosisCompareBasisSchema = z.object({
  current_created_at: z.string(),
  current_generated_at: ns,
  previous_created_at: ns,
  previous_generated_at: ns,
  recomputed: z.boolean(),
  note: ns,
}) satisfies z.ZodType<DiagnosisCompareBasis, unknown>

export const diagnosisMetricChangesSchema = z.object({
  cash_balance: metricChangeSchema,
  cash_weight_pct: metricChangeSchema,
  stock_eval_amount: metricChangeSchema,
  holding_count: metricChangeSchema,
  top1_weight_pct: metricChangeSchema,
  top3_weight_pct: metricChangeSchema,
  herfindahl_index: metricChangeSchema,
  total_profit_loss_pct: metricChangeSchema,
}) satisfies z.ZodType<DiagnosisMetricChanges, unknown>

const valuePair = z.object({ previous: ns, current: ns })

export const exposureChangesSchema = z.object({
  calc_version: valuePair,
  valuation_date: valuePair,
  summary: z.record(z.string(), metricChangeSchema),
  look_through_top1: z.unknown(),
  companies: z.array(
    z.object({
      name: z.string(),
      code: ns,
      previous_total_weight_pct: nn,
      current_total_weight_pct: nn,
      change_pct: nn,
      previous_total_amount: nn,
      current_total_amount: nn,
      status: z.enum(['added', 'removed', 'changed']),
    }),
  ),
  note_code: ns,
  companies_compared: ns,
}) satisfies z.ZodType<ExposureChanges, unknown>
