<script setup lang="ts">
import { cn } from '@/lib/utils';
import type { RegionNode } from '@/lib/region-data';

const props = defineProps<{
  id?: string;
  value: string;
  placeholder: string;
  options: RegionNode[];
  disabled?: boolean;
}>();

const emit = defineEmits<{ 'update:value': [v: string] }>();
</script>

<template>
  <select
    :id="props.id"
    :value="props.value"
    :disabled="props.disabled"
    :class="
      cn(
        'w-full h-10 px-2.5 rounded-[10px] bg-card border text-[13.5px] outline-none transition-colors',
        'focus:border-primary focus:ring-2 focus:ring-primary/20',
        props.disabled ? 'bg-bg-inset text-text-tertiary border-border cursor-not-allowed' : 'border-border-strong text-text-primary',
      )
    "
    @change="emit('update:value', ($event.target as HTMLSelectElement).value)"
  >
    <option value="">{{ props.placeholder }}</option>
    <option v-for="o in props.options" :key="o.code" :value="o.code">{{ o.name }}</option>
  </select>
</template>
