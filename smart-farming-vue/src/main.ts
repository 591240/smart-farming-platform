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
