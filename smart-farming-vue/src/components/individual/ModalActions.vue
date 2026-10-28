<script setup lang="ts">
import { computed } from 'vue';
import { cn } from '@/lib/utils';

const props = withDefaults(
  defineProps<{
    loading: boolean;
    submitText: string;
    disabled?: boolean;
    /** fade：个体页原样式（loading 时主色淡化）；muted：写档弹窗原样式（不可用时置灰） */
    inactiveStyle?: 'fade' | 'muted';
  }>(),
  { disabled: false, inactiveStyle: 'fade' },
);

const emit = defineEmits<{ cancel: []; submit: [] }>();

const submitClass = computed(() => {
  if (props.inactiveStyle === 'muted') {
    return cn(
      'px-5 py-2 rounded-[10px] text-[14px] text-white font-medium transition-colors flex items-center gap-2',
      props.disabled || props.loading ? 'bg-bg-inset text-text-disabled cursor-not-allowed' : 'bg-primary hover:bg-primary-600',
    );
  }
  return cn(
    'px-5 py-2 rounded-[10px] text-[14px] text-white bg-primary hover:bg-primary-600 font-medium transition-colors flex items-center gap-2',
    props.loading && 'opacity-70 cursor-not-allowed',
  );
});
</script>

<template>
  <div class="flex justify-end gap-3">
    <button
      class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
      @click="emit('cancel')"
    >
      取消
    </button>
    <button :disabled="disabled || loading" :class="submitClass" @click="emit('submit')">
      <span
        v-if="loading"
        class="w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin"
      />{{ loading ? '提交中…' : submitText }}
    </button>
  </div>
</template>
