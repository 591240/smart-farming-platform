<script setup lang="ts">
import { ref, watch } from 'vue';
import { apiListVarieties } from '@/api/auth-api';

const props = defineProps<{
  id?: string;
  speciesCode: string;
  value: string;
}>();
const emit = defineEmits<{ 'update:value': [v: string] }>();

const items = ref<{ variety_code: string; variety_name: string }[]>([]);
const loading = ref(true);

watch(
  () => props.speciesCode,
  (code, _old, onCleanup) => {
    let cancelled = false;
    loading.value = true;
    apiListVarieties(code || 'pig')
      .then((res) => {
        if (!cancelled) items.value = res.items;
      })
      .catch(() => {
        if (!cancelled) items.value = [{ variety_code: 'common', variety_name: '通用' }];
      })
      .finally(() => {
        if (!cancelled) loading.value = false;
      });
    onCleanup(() => {
      cancelled = true;
    });
  },
  { immediate: true },
);
</script>

<template>
  <select
    :id="props.id"
    :value="props.value"
    :disabled="loading"
    class="w-full h-10 px-3 rounded-[10px] bg-card border border-border-strong text-[14px] text-text-primary outline-none focus:border-primary transition-colors disabled:text-text-tertiary"
    @change="emit('update:value', ($event.target as HTMLSelectElement).value)"
  >
    <option v-if="loading">加载中…</option>
    <template v-else>
      <option v-for="v in items" :key="v.variety_code" :value="v.variety_code">{{ v.variety_name }}</option>
    </template>
  </select>
</template>
