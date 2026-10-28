<script setup lang="ts">
import { cn } from '@/lib/utils';
import { inputCls } from './styles';
import type { PenReconcileItem } from '@/api/individual-api';

const props = defineProps<{
  pens: PenReconcileItem[];
  value: number | '';
  id: string;
}>();

const emit = defineEmits<{ 'update:value': [v: number] }>();

function handleChange(e: Event) {
  emit('update:value', Number((e.target as HTMLSelectElement).value));
}

function penOptionText(p: PenReconcileItem): string {
  return `${p.pen_name}（存栏 ${p.stock_total} / 在栏耳标 ${p.active_total}${p.diff > 0 ? ` · 可补 ${p.diff}` : ''}）`;
}
</script>

<template>
  <select :id="props.id" :value="props.value" :class="cn(inputCls, 'mb-4')" @change="handleChange">
    <option value="">选择圈舍</option>
    <option v-for="p in props.pens" :key="p.pen_id" :value="p.pen_id">{{ penOptionText(p) }}</option>
  </select>
</template>
