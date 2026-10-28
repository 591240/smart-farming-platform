<script setup lang="ts">
// ===== 危险操作二次确认 =====
import { onMounted, ref } from 'vue';
import { cn } from '@/lib/utils';

const props = defineProps<{
  title: string;
  confirmText: string;
  action: () => void | Promise<void>;
}>();

const emit = defineEmits<{ cancel: [] }>();

const loading = ref(false);
const cancelBtn = ref<HTMLButtonElement | null>(null);

onMounted(() => cancelBtn.value?.focus());

async function handleConfirm() {
  loading.value = true;
  try {
    await props.action();
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center" @click="emit('cancel')">
    <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
    <div
      class="relative bg-card rounded-[16px] w-[420px] max-w-[90vw] p-6 animate-modal-in"
      style="box-shadow: var(--e3)"
      @click.stop
    >
      <h2 class="text-[16px] font-semibold text-text-primary mb-3">{{ title }}</h2>
      <p class="text-[13.5px] text-text-secondary leading-[1.7]"><slot /></p>
      <div class="flex justify-end gap-3 mt-6">
        <button
          ref="cancelBtn"
          class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
          @click="emit('cancel')"
        >
          取消
        </button>
        <button
          :disabled="loading"
          :class="
            cn(
              'px-5 py-2 rounded-[10px] text-[14px] text-white font-medium transition-colors',
              loading && 'opacity-70 cursor-not-allowed',
            )
          "
          style="background-color: var(--danger)"
          @click="handleConfirm"
        >
          {{ loading ? '处理中…' : confirmText }}
        </button>
      </div>
    </div>
  </div>
</template>
