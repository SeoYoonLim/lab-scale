import { z } from 'zod'

// GET/PUT /api/auth/preferences (핸드오프 I18N_HISTORY 2-2절)
export interface Preferences {
  preferred_locale: string
  // null = 기본(Asia/Seoul)
  preferred_timezone: string | null
  effective_timezone: string
  supported_locales: string[]
  default_locale: string
}

export interface PreferencesInput {
  preferred_locale?: string
  // null을 보내면 기본(Asia/Seoul)으로 돌아간다. 필드를 보내지 않으면 그대로 둔다.
  preferred_timezone?: string | null
}

export const preferencesSchema = z.object({
  preferred_locale: z.string(),
  preferred_timezone: z.string().nullable(),
  effective_timezone: z.string(),
  supported_locales: z.array(z.string()),
  default_locale: z.string(),
}) satisfies z.ZodType<Preferences>
