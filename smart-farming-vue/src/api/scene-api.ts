// F15 场景照片识别（拍照建 3D 场景）
// mock 期：mock-backend 返回典型场舍配置；切真：qwen3.8-flash 多模态结构化输出同构 JSON
import { apiFetch } from './client';

export type PhotoSpecies = 'pig' | 'cattle' | 'sheep';

export interface PhotoSceneConfig {
  species: PhotoSpecies;
  /** 每列栏位数（2~6，超出夹取） */
  pen_count: number;
  /** 墙体颜色（hex） */
  wall_color: string;
  /** 屋面颜色（hex） */
  roof_color: string;
  has_silo: boolean;
  has_fans: boolean;
  has_cool_pad: boolean;
  has_yard: boolean;
  barn_name: string;
}

export interface SceneAnalyzeResult {
  config: PhotoSceneConfig;
  /** 识别置信度 0~1（切真取模型自评分） */
  confidence: number;
  /** 人类可读识别摘要 */
  summary: string;
}

/** 上传场舍照片（dataUrl，数量不限），返回参数化 3D 场景配置；后端会把结果落库（analysis_id） */
export function apiAnalyzeScenePhoto(images: string[]) {
  return apiFetch<SceneAnalyzeResult & { analysis_id?: number }>('POST', '/scene/analyze-photo', { images });
}

export interface SceneLatestResult {
  has_scene: boolean;
  analysis_id?: number;
  config?: PhotoSceneConfig;
  summary?: string;
  confidence?: number;
  image_count?: number;
  model?: string;
  create_time?: string;
}

/** 最近一次识别结果（进页面先恢复它——此前结果只在 useState 里，刷新即空） */
export function apiSceneLatest() {
  return apiFetch<SceneLatestResult>('GET', '/scene/latest');
}
