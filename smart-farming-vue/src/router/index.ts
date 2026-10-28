import { createRouter, createWebHistory } from 'vue-router';
import { apiMe } from '@/api/auth-api';
import { readAuthSnapshot } from '@/api/client';
import LayoutShell from '@/components/layout/LayoutShell.vue';

/**
 * F10 路由守卫（FR-06）：
 * - farmer：无凭证→login；未完成引导→onboarding
 * - vet：仅 /vet-review、/profile 可进，直达其余 URL → 跳待复核页 +「无权限访问」提示
 * - admin：仅 /admin、/profile 可进，同上跳后台管理
 * 体验层拦截；接口层 40301 才是安全边界。
 */
const ROLE_ALLOWED: Record<string, string[]> = {
  vet: ['/vet-review', '/profile'],
  admin: ['/admin', '/profile'],
};
const ROLE_HOME: Record<string, string> = { vet: '/vet-review', admin: '/admin' };

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
    { path: '/onboarding', name: 'onboarding', component: () => import('@/views/OnboardingView.vue') },
    { path: '/screen', name: 'screen', component: () => import('@/views/ScreenView.vue') },
    {
      path: '/',
      component: LayoutShell,
      meta: { roleScoped: true },
      children: [
        { path: '', redirect: '/dashboard' },
        { path: 'dashboard', name: 'dashboard', component: () => import('@/views/DashboardView.vue') },
        { path: 'showcase3d', name: 'showcase3d', component: () => import('@/views/Showcase3dView.vue') },
        { path: 'farm', name: 'farm', component: () => import('@/views/FarmView.vue') },
        { path: 'individuals', name: 'individuals', component: () => import('@/views/IndividualsView.vue') },
        { path: 'breeding', name: 'breeding', component: () => import('@/views/BreedingView.vue') },
        { path: 'slaughter', name: 'slaughter', component: () => import('@/views/SlaughterView.vue') },
        { path: 'device', name: 'device', component: () => import('@/views/DeviceView.vue') },
        { path: 'feeding', name: 'feeding', component: () => import('@/views/FeedingView.vue') },
        { path: 'records', name: 'records', component: () => import('@/views/RecordsView.vue') },
        { path: 'ai-chat', name: 'ai-chat', component: () => import('@/views/AiChatView.vue') },
        { path: 'kb', name: 'kb', component: () => import('@/views/KbView.vue') },
        { path: 'alerts', name: 'alerts', component: () => import('@/views/AlertsView.vue') },
        { path: 'vet-review', name: 'vet-review', component: () => import('@/views/VetReviewView.vue') },
        { path: 'admin', name: 'admin', component: () => import('@/views/AdminView.vue') },
        { path: 'profile', name: 'profile', component: () => import('@/views/ProfileView.vue') },
        { path: 'settings', name: 'settings', component: () => import('@/views/SettingsView.vue') },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
});

router.beforeEach(async (to) => {
  if (to.meta.public) {
    // FR-03：已登录用户不再进入登录页
    if (to.name === 'login' && readAuthSnapshot()) return { path: '/dashboard', replace: true };
    return true;
  }
  const snapshot = readAuthSnapshot();
  if (!snapshot) return { path: '/login', replace: true };

  if (to.name === 'onboarding') {
    // guide_done 唯一来源是真后端 /user/me；取不到就留在引导页，不猜
    let guideDone = false;
    try {
      guideDone = (await apiMe()).guide_done;
    } catch {
      guideDone = false;
    }
    if (guideDone) return { path: '/dashboard', replace: true };
    return true;
  }

  if (to.meta.roleScoped) {
    const allow = ROLE_ALLOWED[snapshot.role];
    if (allow) {
      if (!allow.some((p) => to.path === p || to.path.startsWith(p + '/'))) {
        sessionStorage.setItem('sf_route_denied', '1');
        return { path: ROLE_HOME[snapshot.role], replace: true };
      }
      return true;
    }
    // farmer：guide_done 判定；取不到时放行（连不上不等于没引导）
    let guideDone = true;
    try {
      guideDone = (await apiMe()).guide_done;
    } catch {
      /* 见上 */
    }
    if (!guideDone) return { path: '/onboarding', replace: true };
  }
  return true;
});

export default router;
