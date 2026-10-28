// 认证 API — 按《03-API接口设计》2.1~2.4 / 4.1 / 4.2 契约封装
// F01 范围：register / login / main-species / password / farm / pen / variety

import { apiFetch } from './client';

export interface AuthUser {
  user_id: number;
  phone: string; // 已脱敏（138****6688）
  nickname: string;
  role: 'farmer' | 'vet' | 'admin';
  main_species_code: string | null;
  guide_done: boolean;
}

export interface LoginResult {
  token: string;
  expires_in: number;
  user: AuthUser;
}

// FR-01 注册
export function apiRegister(body: { phone: string; password: string; nickname?: string }) {
  return apiFetch<{ user_id: number; phone: string; first_login: boolean }>('POST', '/auth/register', body);
}

// FR-02 登录
export function apiLogin(body: { phone: string; password: string }) {
  return apiFetch<LoginResult>('POST', '/auth/login', body);
}

// 当前用户信息（登录态恢复用；后端实现时补 /user/me 契约）
export function apiMe() {
  return apiFetch<AuthUser>('GET', '/user/me');
}

// FR-04 / FR-08 修改主物种
export function apiUpdateMainSpecies(mainSpeciesCode: string) {
  return apiFetch<AuthUser>('PUT', '/user/main-species', { main_species_code: mainSpeciesCode });
}

// FR-09 修改密码
export function apiUpdatePassword(body: { old_password: string; new_password: string }) {
  return apiFetch<{ updated: boolean }>('PUT', '/user/password', body);
}

// FR-05 创建养殖场
export function apiCreateFarm(body: {
  farm_name: string;
  province: string;
  city: string;
  county: string;
  adcode: string;
  address?: string;
  contact_person?: string;
  phone?: string;
}) {
  return apiFetch<{ farm_id: number; guide_done: boolean }>('POST', '/farm', body);
}

// FR-06 创建圈舍
export function apiCreatePen(body: {
  farm_id: number;
  pen_name: string;
  variety_code: string | null;
  male_count: number;
  female_count: number;
  batch_stage: string | null;
}) {
  return apiFetch<{ pen_id: number }>('POST', '/pen', body);
}

// FR-06 品种联动（含"通用"兜底）
export function apiListVarieties(speciesCode: string) {
  return apiFetch<{ items: { variety_code: string; variety_name: string }[] }>(
    'GET',
    `/variety/list?species_code=${encodeURIComponent(speciesCode)}`,
  );
}

// ===== 账户数据查询（看板/场舍页数据源，阶段 2 纵切的前置打通）=====

export interface UserFarm {
  farm_id: number;
  farm_name: string;
  location: string;
  address: string;
  species_code: string;
  pen_count: number;
  total_male: number;
  total_female: number;
  create_time: string;
}

export interface UserPen {
  pen_id: number;
  pen_name: string;
  species_code: string;
  variety_code: string | null;
  variety_name: string;
  male_count: number;
  female_count: number;
  batch_stage: string | null;
  create_time: string;
}

export function apiListFarms() {
  return apiFetch<{ items: UserFarm[] }>('GET', '/farm/list');
}

export function apiListPens(farmId: number) {
  return apiFetch<{ items: UserPen[] }>('GET', `/pen/list?farm_id=${farmId}`);
}
