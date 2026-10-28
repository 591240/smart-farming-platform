<script setup lang="ts">
import { computed } from 'vue';

const props = defineProps<{
  real: boolean;
  count: number;
}>();

const cls = computed(() =>
  props.count === 0 ? 'text-text-tertiary' : props.real ? 'text-success' : 'text-warning',
);
const text = computed(() =>
  props.count === 0 ? '未接入设备' : props.real ? '真实设备上行' : '历史模拟数据',
);
</script>

<template>
  <span
    class="flex items-center gap-1.5 text-[11.5px] px-2.5 h-[26px] rounded-full border bg-card"
    :class="cls"
    style="border-color: color-mix(in oklab, currentColor 35%, transparent)"
  >
    <span class="w-1.5 h-1.5 rounded-full bg-current animate-breathe" />
    {{ text }}
  </span>
</template>
