<script setup lang="ts">
/** 首字未达时的等待反馈：跳动三点 + 阶段文案（避免空气泡被误判成"没回复"） */
defineProps<{ stage?: string }>();
</script>

<template>
  <span class="inline-flex items-center gap-2 py-0.5">
    <span class="inline-flex items-center gap-1" aria-hidden="true">
      <span
        v-for="i in [0, 1, 2]"
        :key="i"
        class="w-[5px] h-[5px] rounded-full bg-primary animate-bounce"
        :style="{ animationDelay: `${i * 0.16}s`, animationDuration: '1s' }"
      />
    </span>
    <span class="text-[13px] text-text-tertiary">{{ stage ?? '正在生成回复…' }}</span>
  </span>
</template>
