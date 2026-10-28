<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { Bell, ChevronDown, LogOut, Search } from 'lucide-vue-next';
import { apiListAlerts } from '@/api/device-api';
import { subscribeAlerts } from '@/lib/alert-stream';
import { useAuthStore } from '@/stores/auth';
import { usePrimaryFarm } from '@/composables/use-farm-data';
import { speciesName } from '@/lib/species-data';

const router = useRouter();
const qc = useQueryClient();
const auth = useAuthStore();
const primaryFarm = usePrimaryFarm();

// F03 FR-06/07：待处理告警计数（未读+未处理），WebSocket 新告警实时刷新红点
const { data: alertData } = useQuery({
  queryKey: ['alerts', 'topbar'],
  queryFn: () => apiListAlerts({ status: 'pending', page_size: 1 }),
  refetchInterval: 60_000,
});
const unreadCount = computed(() => alertData.value?.total ?? 0);

const displayName = computed(() => auth.user?.nickname ?? '未登录');
const avatarChar = computed(() => displayName.value.slice(0, 1));

const menuOpen = ref(false);
const menuRef = ref<HTMLElement | null>(null);

function onDocClick(e: MouseEvent) {
  if (menuRef.value && !menuRef.value.contains(e.target as Node)) menuOpen.value = false;
}
function onEsc(e: KeyboardEvent) {
  if (e.key === 'Escape') menuOpen.value = false;
}

let unsubscribe: (() => void) | null = null;
onMounted(() => {
  document.addEventListener('mousedown', onDocClick);
  document.addEventListener('keydown', onEsc);
  unsubscribe = subscribeAlerts(() => qc.invalidateQueries({ queryKey: ['alerts', 'topbar'] }));
});
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocClick);
  document.removeEventListener('keydown', onEsc);
  unsubscribe?.();
});

function handleLogout() {
  menuOpen.value = false;
  auth.logout();
  router.replace('/login');
}
</script>

<template>
  <header class="h-[60px] bg-card flex items-center justify-between px-8 shrink-0" :style="{ boxShadow: 'var(--e-sidebar)' }">
    <!-- Left: Farm switcher -->
    <div class="flex items-center gap-3">
      <button class="flex items-center gap-2 px-3 py-1.5 rounded-[10px] hover:bg-bg-subtle transition-colors duration-[var(--duration-fast)]">
        <div class="w-2 h-2 rounded-full bg-success animate-breathe" />
        <span class="text-[14px] font-medium text-text-primary">
          {{ primaryFarm?.farm_name ?? '我的养殖场' }}
        </span>
        <ChevronDown :size="14" class="text-text-tertiary" />
      </button>
    </div>

    <!-- Center: Search -->
    <div class="hidden lg:flex items-center gap-2 px-3 py-1.5 bg-bg-subtle rounded-[10px] w-[320px]">
      <Search :size="16" class="text-text-tertiary" :stroke-width="1.5" />
      <input
        type="text"
        placeholder="搜索圈舍、耳标号、告警..."
        class="bg-transparent border-none outline-none text-[14px] text-text-primary placeholder:text-text-tertiary w-full"
      />
    </div>

    <!-- Right: Alerts + User -->
    <div class="flex items-center gap-4">
      <button class="relative p-2 rounded-[10px] hover:bg-bg-subtle transition-colors duration-[var(--duration-fast)]">
        <Bell :size="20" :stroke-width="1.5" class="text-text-secondary" />
        <span
          v-if="unreadCount > 0"
          class="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-danger text-white text-[11px] font-medium px-1 tnum"
        >
          {{ unreadCount }}
        </span>
      </button>

      <!-- 账户菜单（FR-10） -->
      <div ref="menuRef" class="relative">
        <button
          class="flex items-center gap-2.5 cursor-pointer rounded-[10px] px-1.5 py-1 hover:bg-bg-subtle transition-colors duration-[var(--duration-fast)]"
          @click="menuOpen = !menuOpen"
        >
          <div class="w-8 h-8 rounded-full bg-primary-100 flex items-center justify-center">
            <span class="text-[13px] font-medium text-primary-700">{{ avatarChar }}</span>
          </div>
          <span class="text-[14px] text-text-secondary hidden md:block">{{ displayName }}</span>
          <ChevronDown :size="14" class="text-text-tertiary" />
        </button>

        <div
          v-if="menuOpen"
          class="absolute right-0 top-[calc(100%+6px)] z-50 w-56 rounded-[12px] border border-border bg-popover p-1 shadow-[var(--e3)] animate-slide-in"
        >
          <div class="px-2 py-1.5">
            <div class="text-[14px] font-medium text-text-primary">{{ displayName }}</div>
            <div class="text-[12px] text-text-tertiary tnum">{{ auth.user?.phone ?? '—' }}</div>
            <div class="mt-1 text-[11.5px] text-text-tertiary">
              主物种：{{ speciesName(auth.user?.main_species_code ?? null) }}
            </div>
          </div>
          <div class="-mx-1 my-1 h-px bg-border" />
          <button
            class="w-full text-left px-2 py-1.5 rounded-[6px] text-[14px] cursor-pointer hover:bg-bg-subtle"
            @click="menuOpen = false; router.push('/profile')"
          >
            个人中心
          </button>
          <div class="-mx-1 my-1 h-px bg-border" />
          <button
            class="w-full text-left px-2 py-1.5 rounded-[6px] text-[14px] cursor-pointer text-danger hover:bg-red-50 flex items-center gap-1.5"
            @click="handleLogout"
          >
            <LogOut :size="14" :stroke-width="1.5" />
            退出登录
          </button>
        </div>
      </div>
    </div>
  </header>
</template>
