<script setup lang="ts">
// ===== 通用弹窗壳与表单件（与场舍管理页同款）=====
import { X } from 'lucide-vue-next';
import { cn } from '@/lib/utils';

withDefaults(
  defineProps<{
    title: string;
    subtitle?: string;
    /** 副标题下边距（个体页为 mb-5，写档弹窗为 mb-4） */
    subtitleClass?: string;
  }>(),
  { subtitleClass: 'mb-5' },
);

const emit = defineEmits<{ close: [] }>();
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center" @click="emit('close')">
    <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
    <div
      class="relative bg-card rounded-[16px] w-[500px] max-w-[90vw] p-6 animate-modal-in max-h-[90vh] overflow-y-auto"
      style="box-shadow: var(--e3)"
      @click.stop
    >
      <div class="flex items-center justify-between mb-1.5">
        <h2 class="text-[16px] font-semibold text-text-primary">{{ title }}</h2>
        <button class="p-1 rounded-[6px] hover:bg-bg-subtle transition-colors" aria-label="关闭" @click="emit('close')">
          <X :size="18" class="text-text-tertiary" :stroke-width="1.5" />
        </button>
      </div>
      <p v-if="subtitle" :class="cn('text-[12.5px] text-text-tertiary', subtitleClass)">{{ subtitle }}</p>
      <slot />
    </div>
  </div>
</template>
