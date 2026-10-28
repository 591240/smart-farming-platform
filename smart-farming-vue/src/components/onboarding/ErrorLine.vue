<script setup lang="ts">
defineProps<{ msg: string }>();
</script>

<template>
  <div
    class="mt-4 px-3.5 py-2.5 rounded-[10px] text-[13px] animate-slide-in"
    style="background-color: rgba(217,83,79,.07); border: 1px solid rgba(217,83,79,.25); color: var(--danger)"
    role="alert"
  >
    {{ msg }}
  </div>
</template>
