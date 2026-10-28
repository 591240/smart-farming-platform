<script setup lang="ts">
defineProps<{ label: string; value: number; tone: string }>();
</script>

<template>
  <div class="rounded-[8px] px-2.5 py-2 text-center bg-bg-subtle">
    <div class="tnum text-[20px] font-semibold leading-tight" :class="tone">{{ value }}</div>
    <div class="text-[10.5px] mt-0.5 text-text-secondary">{{ label }}</div>
  </div>
</template>
