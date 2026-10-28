<script setup lang="ts">
// ===== 录入耳标弹窗（单个 / 批量两页签）=====
import { ref } from 'vue';
import { Layers, Tag } from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import type { PenReconcileItem } from '@/api/individual-api';
import Modal from './Modal.vue';
import SingleIndividualForm from './SingleIndividualForm.vue';
import BatchIndividualForm from './BatchIndividualForm.vue';

defineProps<{
  pens: PenReconcileItem[];
  defaultPenId?: number;
}>();

const emit = defineEmits<{ close: []; saved: [msg: string] }>();

const tab = ref<'single' | 'batch'>('single');

const tabs = [
  { v: 'single', label: '单个建档', icon: Tag },
  { v: 'batch', label: '批量建档', icon: Layers },
] as const;

function handleSaved(msg: string) {
  emit('saved', msg);
}
</script>

<template>
  <Modal
    title="录入耳标"
    subtitle="耳标号场内唯一；建档只补个体档案，不动存栏（新入栏动物请走「调整存栏」）"
    @close="emit('close')"
  >
    <div class="grid grid-cols-2 gap-2 mb-5">
      <button
        v-for="o in tabs"
        :key="o.v"
        type="button"
        :class="
          cn(
            'flex items-center justify-center gap-1.5 py-2 rounded-[10px] text-[13px] font-medium border transition-all duration-[var(--duration-fast)]',
            tab === o.v
              ? 'bg-primary-50 text-primary-700 border-primary'
              : 'bg-bg-subtle text-text-secondary border-transparent hover:bg-bg-inset',
          )
        "
        @click="tab = o.v"
      >
        <component :is="o.icon" :size="14" :stroke-width="1.5" />{{ o.label }}
      </button>
    </div>
    <SingleIndividualForm
      v-if="tab === 'single'"
      :pens="pens"
      :default-pen-id="defaultPenId"
      @close="emit('close')"
      @saved="handleSaved"
    />
    <BatchIndividualForm
      v-else
      :pens="pens"
      :default-pen-id="defaultPenId"
      @close="emit('close')"
      @saved="handleSaved"
    />
  </Modal>
</template>
