<script setup lang="ts">
import { computed, type Component } from 'vue';

const props = withDefaults(
  defineProps<{
    icon: Component;
    label: string;
    value: number;
    unit?: string;
    sub: string;
    accent: string;
    digits?: number;
    fresh?: string;
  }>(),
  { digits: 0, unit: undefined, fresh: undefined },
);

const shown = computed(() => (props.digits > 0 ? props.value.toFixed(props.digits) : Math.round(props.value)));
</script>

<template>
  <div class="bg-card border border-border rounded-[12px] card-highlight px-4 py-3 flex flex-col min-w-0">
    <div class="flex items-center gap-1.5 shrink-0 text-text-secondary min-w-0">
      <component :is="props.icon" :size="13" :stroke-width="1.5" :class="props.accent" />
      <span class="text-[11.5px] truncate">{{ props.label }}</span>
      <span class="flex-1" />
      <span v-if="props.fresh" class="text-[10px] text-text-tertiary shrink-0">{{ props.fresh }}</span>
    </div>
    <div class="flex items-baseline gap-1.5 mt-1.5 shrink-0">
      <span class="tnum text-[30px] font-semibold leading-none" :class="props.accent">{{ shown }}</span>
      <span v-if="props.unit" class="text-[12px] text-text-secondary">{{ props.unit }}</span>
    </div>
    <p class="text-[10.5px] mt-1.5 truncate text-text-tertiary">{{ props.sub }}</p>
  </div>
</template>
