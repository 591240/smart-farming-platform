<script setup lang="ts">
import type { Component, CSSProperties } from 'vue';

const props = defineProps<{
  icon: Component;
  title: string;
  fresh?: string;
  compact?: boolean;
  panelStyle?: CSSProperties;
}>();
</script>

<template>
  <div class="bg-card border border-border rounded-[12px] card-highlight flex flex-col min-h-0" :class="props.compact && 'shrink-0'" :style="props.panelStyle">
    <div class="flex items-center gap-2 mb-2 shrink-0 px-3.5 pt-3">
      <component :is="props.icon" :size="15" :stroke-width="1.5" class="text-primary" />
      <h2 class="text-[13.5px] font-semibold tracking-wide text-text-primary">{{ props.title }}</h2>
      <span class="flex-1" />
      <slot name="extra" />
      <span v-if="props.fresh" class="text-[10px] text-text-tertiary">{{ props.fresh }}</span>
    </div>
    <div class="flex-1 min-h-0 flex flex-col px-3.5 pb-3">
      <slot />
    </div>
  </div>
</template>
