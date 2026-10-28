<script setup lang="ts">
defineProps<{ text: string }>();
</script>

<template>
  <div class="flex-1 min-h-0 flex items-center justify-center py-6">
    <p class="text-[12px] text-center px-4 text-text-tertiary">{{ text }}</p>
  </div>
</template>
