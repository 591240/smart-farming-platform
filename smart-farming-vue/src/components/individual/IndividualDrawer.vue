<script setup lang="ts">
// ===== F17 个体档案抽屉（一畜一档：信息卡 + 体重曲线 + 事件时间线）=====
import { computed, ref, watch } from 'vue';
import { Pencil, Plus, X } from 'lucide-vue-next';
import { ApiError } from '@/lib/api-contract';
import { apiIndividualDetail, type IndividualDetail, type IndividualItem } from '@/api/individual-api';
import StatusBadge from './StatusBadge.vue';
import WeightCurve from './WeightCurve.vue';
import TimelineItem from './TimelineItem.vue';
import AddEventDialog from './AddEventDialog.vue';

const props = defineProps<{ individual: IndividualItem }>();

const emit = defineEmits<{
  close: [];
  edit: [item: IndividualItem];
  toast: [msg: string];
}>();

const detail = ref<IndividualDetail | null>(null);
const loading = ref(true);
const eventOpen = ref(false);

async function reload() {
  loading.value = true;
  try {
    detail.value = await apiIndividualDetail(props.individual.individual_id);
  } catch (e) {
    emit('toast', e instanceof ApiError ? e.message : '档案加载失败');
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.individual.individual_id,
  () => {
    void reload();
  },
  { immediate: true },
);

const info = computed(() => detail.value?.individual ?? props.individual);

async function handleTimelineDeleted() {
  emit('toast', '记录已删除');
  await reload();
}

async function handleEventSaved() {
  eventOpen.value = false;
  emit('toast', '记录已写入档案');
  await reload();
}
</script>

<template>
  <div class="fixed inset-0 z-50 flex justify-end" @click="emit('close')">
    <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
    <div
      class="relative bg-bg-subtle w-[640px] max-w-[95vw] h-full overflow-y-auto animate-slide-in"
      style="box-shadow: var(--e3)"
      @click.stop
    >
      <!-- 头部信息卡 -->
      <div class="bg-card p-6 border-b border-border sticky top-0 z-10">
        <div class="flex items-start justify-between">
          <div>
            <div class="flex items-center gap-2.5">
              <h2 class="text-[18px] font-semibold text-text-primary tnum">{{ info.ear_tag_no }}</h2>
              <StatusBadge :status="info.status" :label="info.status_label" />
            </div>
            <p class="text-[13px] text-text-secondary mt-1">
              {{ info.gender_label }} · {{ info.pen_name }} · {{ info.variety_name ?? '—' }} · 建档 {{ info.create_time.slice(0, 10) }}
            </p>
          </div>
          <button
            class="p-1 rounded-[6px] hover:bg-bg-subtle transition-colors"
            aria-label="关闭"
            @click="emit('close')"
          >
            <X :size="18" class="text-text-tertiary" :stroke-width="1.5" />
          </button>
        </div>
        <div class="grid grid-cols-2 gap-x-6 gap-y-1.5 mt-4 text-[12.5px]">
          <p class="text-text-tertiary">
            出生日期：<span class="text-text-secondary tnum">{{ info.birth_date ?? '—' }}</span>
          </p>
          <p class="text-text-tertiary">来源：<span class="text-text-secondary">{{ info.source }}</span></p>
          <p class="text-text-tertiary col-span-2">
            外貌特征：<span class="text-text-secondary">{{ info.traits || '—' }}</span>
          </p>
          <p class="text-text-tertiary col-span-2">
            血缘备注：<span class="text-text-secondary">{{ info.lineage || '—' }}</span>
          </p>
          <p v-if="info.status !== 'active' && info.exit_note" class="text-text-tertiary col-span-2">
            离栏：<span class="text-text-secondary">{{ info.exit_note }}（{{ info.exit_time?.slice(0, 10) }}）</span>
          </p>
        </div>
        <div class="flex gap-2.5 mt-4">
          <!-- 离栏个体仍可补录历史记录 -->
          <button
            class="flex items-center gap-1.5 px-3.5 py-1.5 bg-primary text-white rounded-[10px] text-[13px] font-medium hover:bg-primary-600 transition-colors"
            @click="eventOpen = true"
          >
            <Plus :size="14" :stroke-width="1.5" />记一笔
          </button>
          <button
            class="flex items-center gap-1.5 px-3.5 py-1.5 rounded-[10px] text-[13px] font-medium border border-border-strong text-text-secondary hover:bg-bg-subtle transition-colors"
            @click="emit('edit', info)"
          >
            <Pencil :size="14" :stroke-width="1.5" />编辑档案
          </button>
        </div>
      </div>

      <div class="p-6 space-y-6">
        <!-- 体重曲线 -->
        <section class="bg-card rounded-[12px] p-5 card-highlight">
          <h3 class="text-[14px] font-semibold text-text-primary mb-3">生长曲线</h3>
          <p v-if="loading" class="text-[12px] text-text-tertiary py-3">加载中…</p>
          <WeightCurve v-else :series="detail?.weight_series ?? []" />
        </section>

        <!-- 事件时间线 -->
        <section class="bg-card rounded-[12px] p-5 card-highlight">
          <h3 class="text-[14px] font-semibold text-text-primary mb-4">档案时间线</h3>
          <p v-if="loading" class="text-[12px] text-text-tertiary py-3">加载中…</p>
          <p v-else-if="(detail?.events.length ?? 0) === 0" class="text-[12px] text-text-tertiary py-3">
            暂无记录
          </p>
          <ul v-else class="relative">
            <TimelineItem
              v-for="ev in detail?.events ?? []"
              :key="ev.event_id"
              :ev="ev"
              :deleted-action="handleTimelineDeleted"
              @toast="emit('toast', $event)"
            />
          </ul>
        </section>
      </div>

      <AddEventDialog
        v-if="eventOpen"
        :individual-id="info.individual_id"
        :ear-tag-no="info.ear_tag_no"
        :birth-date="info.birth_date"
        @close="eventOpen = false"
        @saved="handleEventSaved"
      />
    </div>
  </div>
</template>
