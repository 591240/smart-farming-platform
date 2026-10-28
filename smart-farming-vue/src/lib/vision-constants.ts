/**
 * F09 视觉事件的枚举与文案 —— 唯一来源。
 *
 * 为什么单独立一个文件而不是放在 mock-backend 里再转导出：这三段文案有"逐字一致"硬约束
 * （RK-4：看板预览卡 / 设备 [预览] 弹窗时间线 / vision 告警卡 三处必须同一字符串），
 * 而 mock 内核是过渡形态、按批次会退役。常量住在引擎里，切真或删除引擎时前端就会断链。
 *
 * 后端 `app/api/vision.py` 的 VISION_EVENT_META 与本文件逐字对应；改这里必须同步改后端，
 * 否则告警文案与前端标签会对不上（`smoke_vision_kb.py` 有中文标签断言兜着）。
 */

export const VISION_EVENT_TYPES = ['huddle', 'water', 'cough', 'lethargy', 'death'] as const;
export type VisionEventType = (typeof VISION_EVENT_TYPES)[number];

export const VISION_EVENT_META: Record<VisionEventType, { label: string; impact: string; advice: string }> = {
  huddle: { label: '扎堆', impact: '密度过高或应激信号', advice: '建议通风降温，排查应激源并适当分群' },
  water: { label: '圈舍积水', impact: '圈舍潮湿，湿度与蹄病风险上升', advice: '建议检修排水，及时清理积水并更换垫料' },
  cough: { label: '咳嗽', impact: '呼吸道疾病前兆信号', advice: '建议尽早 AI 问诊排查（问诊跳转链接 V1.1）' },
  lethargy: { label: '精神萎靡', impact: '精神状态异常，健康风险提示', advice: '建议加强巡查，必要时 AI 问诊排查' },
  death: {
    label: '死亡牲畜（疑似）',
    impact: '发现疑似死亡个体',
    advice: '请立即人工核实，确认后到「场舍管理 → 调整存栏」登记死淘（系统不自动扣减存栏）',
  },
};

/** 免责固定文案（07-协议第四节定稿；三处展示共用此常量，不得各写一份） */
export const VISION_DISCLAIMER = '视觉识别结果仅作为提示，不作为确诊依据';
