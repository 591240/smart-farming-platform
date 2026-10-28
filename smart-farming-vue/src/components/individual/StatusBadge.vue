<script setup lang="ts">
import { computed } from 'vue';
import type { IndividualStatus } from '@/api/individual-api';

const props = defineProps<{ status: IndividualStatus; label: string }>();

const badgeStyle = computed(() =>
  props.status === 'active'
    ? { backgroundColor: 'rgba(58,143,95,.1)', color: 'var(--success, #3a8f5f)' }
    : props.status === 'sold'
      ? { backgroundColor: 'rgba(58,110,165,.1)', color: '#4a7ab0' }
      : { backgroundColor: 'rgba(120,120,120,.1)', color: 'var(--text-tertiary, #8a8a8a)' },
);
</script>

<template>
  <span class="px-2 py-0.5 rounded-[6px] text-[12px] font-medium" :style="badgeStyle">
    {{ label }}
  </span>
</template>
