<script setup lang="ts">
// 通用弹窗壳（对齐场舍管理页风格）+ FormError + ModalActions
// Modal / FormError / ModalActions 三件套合并为一个 SFC：
// - error 非空时渲染错误条（原 FormError）
// - footer 插槽可整体替换底部（原料价格弹窗的「关闭」按钮）
// - 默认底部为「取消 / 提交」双按钮（原 ModalActions）
import { X } from 'lucide-vue-next';
import { cn } from '@/lib/utils';

withDefaults(
  defineProps<{
    title: string;
    subtitle?: string;
    width?: number;
    error?: string | null;
    loading?: boolean;
    submitText?: string;
    submitDisabled?: boolean;
  }>(),
  {
    subtitle: undefined,
    width: undefined,
    error: null,
    loading: false,
    submitText: '',
    submitDisabled: false,
  },
);

const emit = defineEmits<{ close: []; cancel: []; submit: [] }>();
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center" @click="emit('close')">
    <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
    <div
      class="relative bg-card rounded-[16px] w-[520px] max-w-[92vw] p-6 animate-modal-in max-h-[90vh] overflow-y-auto"
      :style="{ boxShadow: 'var(--e3)', ...(width ? { width: `${width}px` } : {}) }"
      @click.stop
    >
      <div class="flex items-center justify-between mb-1.5">
        <h2 class="text-[16px] font-semibold text-text-primary">{{ title }}</h2>
        <button type="button" @click="emit('close')" class="p-1 rounded-[6px] hover:bg-bg-subtle transition-colors" aria-label="关闭">
          <X :size="18" class="text-text-tertiary" :stroke-width="1.5" />
        </button>
      </div>
      <p v-if="subtitle" class="text-[12.5px] text-text-tertiary mb-5">{{ subtitle }}</p>

      <div
        v-if="error"
        class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
        style="background-color: rgba(217,83,79,.07); border: 1px solid rgba(217,83,79,.25); color: var(--danger)"
        role="alert"
      >
        {{ error }}
      </div>

      <slot />

      <slot name="footer">
        <div class="flex justify-end gap-3">
          <button
            type="button"
            @click="emit('cancel')"
            class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
          >
            取消
          </button>
          <button
            type="button"
            @click="emit('submit')"
            :disabled="loading || submitDisabled"
            :class="
              cn(
                'px-5 py-2 rounded-[10px] text-[14px] text-white bg-primary hover:bg-primary-600 font-medium transition-colors flex items-center gap-2',
                (loading || submitDisabled) && 'opacity-70 cursor-not-allowed',
              )
            "
          >
            <span v-if="loading" class="w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin" />
            {{ loading ? '提交中…' : submitText }}
          </button>
        </div>
      </slot>
    </div>
  </div>
</template>
