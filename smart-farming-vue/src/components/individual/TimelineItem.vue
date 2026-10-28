<script setup lang="ts">
import { computed, ref } from 'vue';
import type { Component } from 'vue';
import {
  ArrowRightLeft,
  LogOut,
  Repeat2,
  Scale,
  Stethoscope,
  StickyNote,
  Syringe,
  Tag,
} from 'lucide-vue-next';
import { ApiError } from '@/lib/api-contract';
import {
  apiDeleteIndividualEvent,
  type IndividualEventItem,
  type IndividualEventType,
} from '@/api/individual-api';

const props = defineProps<{
  ev: IndividualEventItem;
  /** 删除成功后的收尾（提示 + 重新拉取档案）；保持原实现的 await 语义 */
  deletedAction?: () => void | Promise<void>;
}>();

const emit = defineEmits<{ toast: [msg: string] }>();

const EVENT_ICON: Record<IndividualEventType, Component> = {
  register: Tag,
  rename: Repeat2,
  transfer: ArrowRightLeft,
  exit: LogOut,
  weight: Scale,
  immunization: Syringe,
  treatment: Stethoscope,
  note: StickyNote,
};

function eventSummary(type: IndividualEventType, p: Record<string, string | number>): string {
  switch (type) {
    case 'register':
      return `来源：${p.source ?? '—'}`;
    case 'rename':
      return `${p.old_no} → ${p.new_no}`;
    case 'transfer':
      return `${p.from_pen} → ${p.to_pen}`;
    case 'exit':
      return String(p.note ?? '');
    case 'weight':
      return `${p.weight_kg} kg${p.note ? ` · ${p.note}` : ''}`;
    case 'immunization':
      return [p.vaccine_name, p.dose, p.next_date ? `下次免疫 ${p.next_date}` : ''].filter(Boolean).join(' · ');
    case 'treatment':
      return [p.symptom, p.diagnosis, p.medication, p.outcome, p.vet_name].filter(Boolean).join(' · ');
    case 'note':
      return String(p.content ?? '');
  }
}

const icon = computed(() => EVENT_ICON[props.ev.event_type] ?? StickyNote);
const summary = computed(() => eventSummary(props.ev.event_type, props.ev.payload));
const deleting = ref(false);

async function handleDelete() {
  deleting.value = true;
  try {
    await apiDeleteIndividualEvent(props.ev.event_id);
    await props.deletedAction?.();
  } catch (e) {
    emit('toast', e instanceof ApiError ? e.message : '删除失败');
  } finally {
    deleting.value = false;
  }
}
</script>

<template>
  <li class="flex gap-3 pb-5 last:pb-0 relative">
    <div class="flex flex-col items-center">
      <span
        class="w-7 h-7 rounded-full flex items-center justify-center shrink-0"
        style="background-color: rgba(58,107,79,.1); color: var(--primary, #3a6b4f)"
      >
        <component :is="icon" :size="14" :stroke-width="1.5" />
      </span>
      <span class="w-px flex-1 bg-border mt-1" />
    </div>
    <div class="flex-1 min-w-0 pb-1">
      <div class="flex items-center gap-2">
        <span class="text-[13.5px] font-medium text-text-primary">{{ ev.event_type_label }}</span>
        <span
          v-if="!ev.manual"
          class="px-1.5 py-px rounded-[4px] text-[10.5px] bg-bg-subtle text-text-tertiary"
          title="来自存栏流水，不可修改"
        >
          档案
        </span>
        <span class="text-[12px] text-text-tertiary tnum ml-auto">{{ ev.event_date }}</span>
      </div>
      <p class="text-[12.5px] text-text-secondary mt-1 leading-[1.6] break-all">{{ summary }}</p>
      <button
        v-if="ev.manual"
        :disabled="deleting"
        class="text-[11.5px] text-text-tertiary underline mt-1.5 hover:text-text-secondary transition-colors disabled:opacity-50"
        @click="handleDelete"
      >
        {{ deleting ? '删除中…' : '删除（纠错）' }}
      </button>
    </div>
  </li>
</template>
