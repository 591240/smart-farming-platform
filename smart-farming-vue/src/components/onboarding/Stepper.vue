<script setup lang="ts">
const props = defineProps<{
  id?: string;
  label: string;
  value: number;
}>();
const emit = defineEmits<{ 'update:value': [v: number] }>();

function onChange(e: Event) {
  const n = Number.parseInt((e.target as HTMLInputElement).value, 10);
  emit('update:value', Number.isFinite(n) ? Math.max(0, Math.min(99999, n)) : 0);
}
</script>

<template>
  <div>
    <label :for="props.id" class="block text-[13px] text-text-secondary mb-1.5">{{ props.label }}</label>
    <div class="flex items-center gap-1.5">
      <button
        type="button"
        :disabled="props.value <= 0"
        :aria-label="`减少${props.label}`"
        class="w-10 h-10 rounded-[10px] bg-bg-subtle border border-border text-text-secondary text-[16px] transition-colors hover:bg-bg-inset disabled:opacity-40 disabled:cursor-not-allowed"
        @click="emit('update:value', Math.max(0, props.value - 1))"
      >
        −
      </button>
      <input
        :id="props.id"
        type="number"
        :min="0"
        :max="99999"
        :value="props.value"
        class="flex-1 h-10 text-center rounded-[10px] bg-card border border-border-strong text-[14px] tnum text-text-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-colors"
        @change="onChange"
      />
      <button
        type="button"
        :aria-label="`增加${props.label}`"
        class="w-10 h-10 rounded-[10px] bg-bg-subtle border border-border text-text-secondary text-[16px] transition-colors hover:bg-bg-inset"
        @click="emit('update:value', Math.min(99999, props.value + 1))"
      >
        +
      </button>
    </div>
  </div>
</template>
