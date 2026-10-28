<script setup lang="ts">
/** 耳标选择器（受控）：搜索在栏个体，单选 */
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useQuery } from '@tanstack/vue-query';
import { Check, Search, X } from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { ApiError } from '@/lib/api-contract';
import { apiListIndividuals } from '@/api/individual-api';
import { fieldCls, inputCls } from './styles';
import type { PickedIndividual } from './types';

const props = defineProps<{ value: PickedIndividual | null }>();

const emit = defineEmits<{ 'update:value': [v: PickedIndividual | null] }>();

const kw = ref('');
const q = ref('');
let debounceTimer: number | null = null;

watch(
  kw,
  (v) => {
    if (debounceTimer) window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(() => {
      q.value = v.trim();
    }, 250);
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  if (debounceTimer) window.clearTimeout(debounceTimer);
});

const { data, isFetching, error } = useQuery({
  queryKey: computed(() => ['individual-pick', q.value]),
  // 与 key 中的 q 保持同一次取值，避免请求发出瞬间又输入导致关键字错位
  queryFn: (ctx) =>
    apiListIndividuals({
      keyword: (ctx.queryKey[1] as string) || undefined,
      status: 'active',
      page_size: 20,
    }),
});

const items = computed(() => data.value?.items ?? []);
const errorMessage = computed(() =>
  error.value ? (error.value instanceof ApiError ? error.value.message : '个体列表读取失败') : '',
);

function pick(it: { individual_id: number; ear_tag_no: string; pen_name: string }) {
  emit('update:value', {
    individual_id: it.individual_id,
    ear_tag_no: it.ear_tag_no,
    pen_name: it.pen_name,
  });
}
</script>

<template>
  <div class="mb-4">
    <label for="ind-kw" :class="fieldCls">
      选择个体 <span style="color: var(--danger)">*</span>
      <span
        v-if="value"
        class="ml-1.5 inline-flex items-center gap-1 px-2 py-0.5 rounded-[6px] bg-primary-50 text-primary-700 text-[12px] font-medium"
      >
        {{ value.ear_tag_no }} · {{ value.pen_name }}<button type="button" class="hover:opacity-70" aria-label="取消选择" @click="emit('update:value', null)">
          <X :size="11" :stroke-width="2" />
        </button>
      </span>
    </label>
    <div class="relative mb-2">
      <Search
        :size="14"
        class="absolute left-3 top-1/2 -translate-y-1/2 text-text-tertiary"
        :stroke-width="1.5"
      />
      <input
        id="ind-kw"
        v-model="kw"
        placeholder="搜耳标号 / 备注"
        :class="cn(inputCls, 'h-9 pl-8 text-[13px]')"
      />
    </div>
    <p v-if="error" class="text-[12.5px] py-3" style="color: var(--danger)">
      {{ errorMessage }}
    </p>
    <div v-else class="max-h-[190px] overflow-y-auto rounded-[10px] border border-border">
      <button
        v-for="it in items"
        :key="it.individual_id"
        type="button"
        :class="
          cn(
            'w-full flex items-center gap-2 px-3 py-2 text-[13px] text-left transition-colors border-b border-border last:border-b-0',
            value?.individual_id === it.individual_id
              ? 'bg-primary-50 text-primary-700 font-medium'
              : 'text-text-primary hover:bg-bg-subtle',
          )
        "
        @click="pick(it)"
      >
        <span class="flex-1 min-w-0 truncate">
          {{ it.ear_tag_no }}<span class="text-text-tertiary font-normal"> · {{ it.pen_name }} · {{ it.gender_label }}</span>
        </span>
        <Check v-if="value?.individual_id === it.individual_id" :size="14" :stroke-width="2" />
      </button>
      <p v-if="items.length === 0" class="text-[12.5px] text-text-tertiary px-3 py-4">
        {{ isFetching ? '搜索中…' : '没有匹配的在栏个体。个体需先在【个体档案】建档。' }}
      </p>
    </div>
  </div>
</template>
