<script setup lang="ts">
import { onMounted } from 'vue';
import { RouterView } from 'vue-router';
import Sidebar from '@/components/layout/Sidebar.vue';
import TopBar from '@/components/layout/TopBar.vue';

// F10：角色越权直达被重定向后的一次性提示（守卫写 sessionStorage，此处消费即清）
onMounted(() => {
  if (sessionStorage.getItem('sf_route_denied') === '1') {
    sessionStorage.removeItem('sf_route_denied');
    const el = document.createElement('div');
    el.textContent = '无权限访问该页面';
    el.setAttribute(
      'style',
      'position:fixed;bottom:32px;left:50%;transform:translateX(-50%);z-index:70;padding:10px 16px;border-radius:10px;background:#2A332C;color:#fff;font-size:13px;box-shadow:var(--e3)',
    );
    document.body.appendChild(el);
    window.setTimeout(() => el.remove(), 2600);
  }
});
</script>

<template>
  <div class="flex h-screen bg-bg-page overflow-hidden">
    <Sidebar />
    <div class="flex-1 flex flex-col min-w-0 overflow-hidden">
      <TopBar />
      <main class="flex-1 overflow-y-auto">
        <RouterView />
      </main>
    </div>
  </div>
</template>
