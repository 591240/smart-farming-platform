// 前端契约层 — 与真后端 `app/core/response.py` / `app/api/records.py` 一一对应。
//
// 这里只放"前后端必须逐字一致"的纯契约物：错误信封类型、输入校验规则、台账类型枚举。
// 它们原先住在 `lib/mock-backend.ts` 里被 14 个文件借用；浏览器内核下线后迁到这里，
// 因为契约与"数据从哪来"无关，不该跟着内核一起消失。

export class ApiError extends Error {
  code: number;
  data: unknown;
  constructor(code: number, message: string, data?: unknown) {
    super(message);
    this.code = code;
    this.data = data;
  }
}

/** 03-API 1.3 错误码表（前端需要按码分支的才列在这里） */
export const API_CODE = {
  OK: 0,
  UNAUTHORIZED: 40101,
  ROLE_FORBIDDEN: 40301,
  FORBIDDEN: 40302,
  NOT_FOUND: 40401,
  VALIDATION: 42201,
  WITHDRAWAL: 40001,
  NEGATIVE_STOCK: 40002,
  CONFIRM_NEEDED: 40005,
  LLM_FAIL: 50003,
} as const;

/** F01 手机号口径：与后端 `app/api/auth.py:is_valid_phone` 逐字符一致 */
export function isValidPhone(phone: string): boolean {
  return /^1[3-9]\d{9}$/.test(phone);
}

/** F01 密码口径：与后端 `validate_password` 同规则，返回 null 表示通过 */
export function validatePassword(password: string): string | null {
  if (password.length < 8) return '密码需 ≥8 位且同时包含字母和数字';
  if (!/[A-Za-z]/.test(password) || !/\d/.test(password)) {
    return '密码需 ≥8 位且同时包含字母和数字';
  }
  return null;
}

/** 台账类型枚举 —— 与后端 `records.py:RECORD_TYPE_LABEL` 同源同序 */
export const RECORD_TYPES = [
  'stock_change',
  'sensor',
  'device_offline',
  'climate',
  'vision',
  'disease',
  'medicine',
  'feed',
  'vet_review',
  'lifecycle',
  'slaughter',
  'individual',
] as const;

export const RECORD_TYPE_LABEL: Record<string, string> = {
  stock_change: '存栏变动',
  sensor: '传感器告警',
  device_offline: '设备离线',
  climate: '气象告警',
  vision: '视觉告警',
  disease: '疫病告警',
  medicine: '用药',
  feed: '饲喂',
  vet_review: '兽医复核',
  lifecycle: '生命周期',
  slaughter: '出栏',
  individual: '个体档案',
};
