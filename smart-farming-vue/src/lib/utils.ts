import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * 本地自然日 YYYY-MM-DD。
 * `new Date().toISOString().slice(0, 10)` 取的是 UTC 日期，东八区 00:00–08:00 会回退一天，
 * 喂养/用药/出栏日期与 `<input type="date" max>` 都因此错位。
 */
export function todayLocal(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
