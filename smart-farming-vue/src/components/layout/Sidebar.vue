<script setup lang="ts">
import { computed } from 'vue';
import { RouterLink, useRoute } from 'vue-router';
import {
  Warehouse,
  Boxes,
  HeartHandshake,
  Truck,
  Cpu,
  MessageSquareHeart,
  Bell,
  UtensilsCrossed,
  NotebookText,
  Tag,
  Settings,
  User,
  ClipboardCheck,
  ShieldCheck,
  BookOpen,
  MonitorPlay,
} from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { readAuthSnapshot } from '@/api/client';

interface NavItem {
  label: string;
  icon: unknown;
  to: string;
  group: string;
  roles?: string[];
}

const navItems: NavItem[] = [
  { label: '数字大屏', icon: MonitorPlay, to: '/dashboard', group: '总览' },
  { label: '场舍三维展示', icon: Boxes, to: '/showcase3d', group: '总览' },
  { label: '场舍管理', icon: Warehouse, to: '/farm', group: '生产管理' },
  { label: '个体耳标', icon: Tag, to: '/individuals', group: '生产管理' },
  { label: '繁殖管理', icon: HeartHandshake, to: '/breeding', group: '生产管理' },
  { label: '出栏登记', icon: Truck, to: '/slaughter', group: '生产管理' },
  { label: '设备管理', icon: Cpu, to: '/device', group: '生产管理' },
  { label: '喂食中心', icon: UtensilsCrossed, to: '/feeding', group: '生产管理' },
  { label: '台账档案', icon: NotebookText, to: '/records', group: '生产管理' },
  { label: 'AI 问诊', icon: MessageSquareHeart, to: '/ai-chat', group: '智能中心' },
  { label: '知识库', icon: BookOpen, to: '/kb', group: '智能中心' },
  { label: '告警中心', icon: Bell, to: '/alerts', group: '智能中心' },
  { label: '待复核病例', icon: ClipboardCheck, to: '/vet-review', group: '智能中心', roles: ['vet'] },
  { label: '后台管理', icon: ShieldCheck, to: '/admin', group: '系统', roles: ['admin'] },
  { label: '个人中心', icon: User, to: '/profile', group: '系统', roles: ['farmer', 'vet', 'admin'] },
  { label: '系统设置', icon: Settings, to: '/settings', group: '系统' },
];

const groups = ['总览', '生产管理', '智能中心', '系统'];

const route = useRoute();
const role = readAuthSnapshot()?.role ?? 'farmer';
const visibleItems = computed(() => navItems.filter((n) => (n.roles ?? ['farmer']).includes(role)));
</script>

<template>
  <aside class="w-[232px] h-screen bg-card flex flex-col shrink-0" :style="{ boxShadow: 'var(--e-sidebar)' }">
    <div class="h-[60px] flex items-center px-6 shrink-0">
      <div class="flex items-center gap-2.5">
        <div class="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
          <Warehouse :size="16" class="text-white" :stroke-width="1.5" />
        </div>
        <span class="text-[16px] font-semibold text-text-primary tracking-tight">云上牧场</span>
      </div>
    </div>

    <nav class="flex-1 px-3 py-2 overflow-y-auto">
      <template v-for="group in groups" :key="group">
        <div v-if="visibleItems.some((n) => n.group === group)" class="mb-4">
          <div class="px-3 py-1.5 text-[12px] font-medium text-text-tertiary uppercase tracking-wider">
            {{ group }}
          </div>
          <RouterLink
            v-for="item in visibleItems.filter((n) => n.group === group)"
            :key="item.to"
            :to="item.to"
            :class="
              cn(
                'relative flex items-center gap-2.5 px-3 py-2 rounded-[10px] mb-0.5 text-[14px] transition-colors',
                'duration-[var(--duration-fast)]',
                route.path === item.to
                  ? 'bg-primary-100 text-primary-700 font-medium'
                  : 'text-text-secondary hover:bg-bg-subtle hover:text-text-primary',
              )
            "
          >
            <span
              v-if="route.path === item.to"
              class="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 bg-primary rounded-r-full"
            />
            <component :is="item.icon" :size="18" :stroke-width="1.5" />
            <span>{{ item.label }}</span>
          </RouterLink>
        </div>
      </template>
    </nav>

    <div class="px-4 py-3 border-t border-border">
      <div class="text-[12px] text-text-tertiary">
        云上牧场 · {{ role === 'vet' ? '执业兽医' : role === 'admin' ? '管理员' : '主账号' }}
      </div>
    </div>
  </aside>
</template>
