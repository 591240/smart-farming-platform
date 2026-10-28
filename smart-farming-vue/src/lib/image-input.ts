/**
 * 图像问诊的输入侧压缩（FR-11）。
 *
 * 为什么必须在浏览器压：`/api/agent/chat` 的 `image` 是 data URL，走 JSON 请求体
 * 还要落进 `agent_message.meta.image`。手机原图 4~8MB，base64 后再涨 33%，
 * 不压就是一次几十 MB 的 POST + 一条同样大的库记录，而且视觉模型也吃不下。
 *
 * 口径与后端注释一致：最长边 ≤512px、JPEG、总体积 ≤300KB（超了降质量重编码一次）。
 * 512 是有意选择——征象识别要的是"皮肤病变/体态/分泌物"这一级特征，不是病理切片，
 * 再大只会增加 token 与延迟，不增加判读依据。
 */

export const MAX_EDGE = 512;
export const JPEG_QUALITY = 0.82;
export const MAX_BYTES = 300 * 1024;

export class ImageInputError extends Error {}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result));
    fr.onerror = () => reject(new ImageInputError('图片读取失败，请重试'));
    fr.readAsDataURL(file);
  });
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new ImageInputError('无法解码这张图片（格式不支持或文件已损坏）'));
    img.src = src;
  });
}

function drawScaled(img: HTMLImageElement, quality: number): string {
  const scale = Math.min(1, MAX_EDGE / Math.max(img.naturalWidth, img.naturalHeight));
  const w = Math.max(1, Math.round(img.naturalWidth * scale));
  const h = Math.max(1, Math.round(img.naturalHeight * scale));
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new ImageInputError('当前浏览器不支持图片压缩');
  ctx.drawImage(img, 0, 0, w, h);
  return canvas.toDataURL('image/jpeg', quality);
}

/** 文件 → 可上传的压缩 data URL。非图片、解码失败、体积超限都会抛 ImageInputError（调用方直接展示） */
export async function fileToImageDataUrl(file: File): Promise<string> {
  if (!file.type.startsWith('image/')) throw new ImageInputError('只能上传图片文件');
  const raw = await readAsDataUrl(file);
  const img = await loadImage(raw);
  let out = drawScaled(img, JPEG_QUALITY);
  if (out.length > MAX_BYTES) out = drawScaled(img, 0.6);
  if (out.length > MAX_BYTES) throw new ImageInputError('图片过大，请换一张或离得更近一些再拍');
  return out;
}
