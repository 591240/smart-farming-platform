// 登录态全局管理（F01）——Pinia 存用户信息、token 与角色。
// token 持久化于 localStorage（sf_token）；40101 由 api/client.ts 统一清凭证并跳登录。

import { defineStore } from 'pinia';
import { ref } from 'vue';
import { apiLogin, apiMe, apiRegister, type AuthUser } from '@/api/auth-api';
import { clearToken, getToken, readAuthSnapshot, setToken, TOKEN_KEY } from '@/api/client';
import { queryClient } from '@/lib/query-client';

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(readAuthSnapshot() ? getToken() : null);
  const user = ref<AuthUser | null>(null);
  /** 启动恢复是否完成（避免守卫误判闪烁） */
  const ready = ref(false);

  async function restore() {
    try {
      if (!getToken() || !readAuthSnapshot()) return;
      const me = await apiMe();
      user.value = me;
      token.value = getToken();
    } catch {
      user.value = null;
      token.value = null;
    } finally {
      ready.value = true;
    }
  }

  async function login(phone: string, password: string): Promise<AuthUser> {
    const result = await apiLogin({ phone, password });
    setToken(result.token);
    token.value = result.token;
    user.value = result.user;
    queryClient.clear();
    return result.user;
  }

  async function register(phone: string, password: string, nickname?: string): Promise<AuthUser> {
    await apiRegister({ phone, password, nickname });
    // FR-01：注册成功自动登录
    const result = await apiLogin({ phone, password });
    setToken(result.token);
    token.value = result.token;
    user.value = result.user;
    queryClient.clear();
    return result.user;
  }

  function setUser(u: AuthUser) {
    user.value = u;
  }

  async function refreshUser(): Promise<AuthUser> {
    const me = await apiMe();
    user.value = me;
    return me;
  }

  function logout() {
    clearToken();
    token.value = null;
    user.value = null;
    queryClient.clear();
  }

  /** 多标签页同步：另一标签退出 → 本标签退出；换账号 → 整页刷新恢复新会话 */
  function bindStorageSync() {
    window.addEventListener('storage', (e: StorageEvent) => {
      if (e.key !== TOKEN_KEY) return;
      if (e.newValue === null) {
        user.value = null;
        token.value = null;
        if (!window.location.pathname.startsWith('/login')) {
          window.location.assign('/login');
        }
      } else if (e.oldValue !== e.newValue) {
        window.location.reload();
      }
    });
  }

  return { token, user, ready, restore, login, register, setUser, refreshUser, logout, bindStorageSync };
});
