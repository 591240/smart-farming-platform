<script setup lang="ts">
defineProps<{
  for?: string;
  text: string;
  required?: boolean;
  optional?: boolean;
}>();
</script>

<template>
  <label :for="$props.for" class="block text-[13px] text-text-secondary mb-1.5">
    {{ text }} <span v-if="required" style="color: var(--danger)">*</span>
    <span v-if="optional" class="text-text-tertiary">（选填）</span>
  </label>
</template>
