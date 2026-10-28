<script setup lang="ts">
defineProps<{ label: string; value: number }>();
</script>

<template>
  <div class="flex flex-col items-center justify-center px-1.5 py-1.5 rounded-[8px] bg-bg-subtle">
    <span class="tnum text-[15px] font-medium text-text-primary">{{ value }}</span>
    <span class="text-[10px] text-text-tertiary">{{ label }}</span>
  </div>
</template>
