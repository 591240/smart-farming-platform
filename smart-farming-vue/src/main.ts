import { createPinia } from 'pinia';
import { VueQueryPlugin } from '@tanstack/vue-query';
import { createApp } from 'vue';
import App from './App.vue';
import router from './router';
import { queryClient } from './lib/query-client';
import { initRevealEngine } from './lib/reveal-engine';
import { useAuthStore } from './stores/auth';
import './styles.css';

// 全局滚动渐入引擎：业务元素只需加 class="reveal"（详见 lib/reveal-engine.ts），勿删
initRevealEngine();

const app = createApp(App);
const pinia = createPinia();
app.use(pinia);
app.use(VueQueryPlugin, { queryClient });
app.use(router);
app.mount('#app');

// 启动恢复登录用户概要（不阻塞首屏；路由守卫另行判定登录态）
useAuthStore(pinia).restore();

// 空闲预热两个大体积页面 chunk（3D 场景 / 数字大屏，均 >500KB）：用户点侧边栏前提前下好，
// 首次点击不再受公网带宽拖累。失败静默——预热只是体验优化，路由守卫照常按需加载
const warmUp = (): void => {
  void import('./views/Showcase3dView.vue');
  void import('./views/DashboardView.vue');
};
const w = window as Window & {
  requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
};
if (typeof w.requestIdleCallback === 'function') w.requestIdleCallback(warmUp, { timeout: 4000 });
else window.setTimeout(warmUp, 2500);
