<script setup lang="ts">
import { computed, ref } from 'vue';
import { cn } from '@/lib/utils';
import { ApiError } from '@/lib/api-contract';
import { apiBatchCreateIndividuals, type IndividualGender, type PenReconcileItem } from '@/api/individual-api';
import { inputCls } from './styles';
import PenSelect from './PenSelect.vue';
import FormError from './FormError.vue';
import ModalActions from './ModalActions.vue';

const props = defineProps<{
  pens: PenReconcileItem[];
  defaultPenId?: number;
}>();

const emit = defineEmits<{ close: []; saved: [msg: string] }>();

const penId = ref<number | ''>(props.defaultPenId ?? '');
const gender = ref<IndividualGender>('male');
const prefix = ref('');
const startNo = ref(1);
const count = ref(1);
const birthDate = ref('');
const err = ref<string | null>(null);
const loading = ref(false);

const selPen = computed(() => props.pens.find((p) => p.pen_id === penId.value));
const effPrefix = computed(() => prefix.value.trim() || (penId.value ? `P${penId.value}` : ''));
const headroom = computed(() => (selPen.value ? (gender.value === 'male' ? selPen.value.diff : selPen.value.diff) : 0));
const preview = computed(() => {
  if (!effPrefix.value || count.value <= 0) return '';
  const head = Array.from(
    { length: Math.min(count.value, 3) },
    (_, i) => `${effPrefix.value}-${gender.value === 'male' ? 'M' : 'F'}${String(startNo.value + i).padStart(3, '0')}`,
  ).join('、');
  return head + (count.value > 3 ? ` … 共 ${count.value} 个` : '');
});

function handleStartInput(e: Event) {
  startNo.value = Math.max(1, parseInt((e.target as HTMLInputElement).value, 10) || 1);
}

function handleCountInput(e: Event) {
  count.value = Math.max(1, parseInt((e.target as HTMLInputElement).value, 10) || 1);
}

async function submit() {
  err.value = null;
  const pid = penId.value;
  const genderVal = gender.value;
  const eff = effPrefix.value;
  const start = startNo.value;
  const cnt = count.value;
  const birth = birthDate.value;
  const pen = selPen.value;
  if (!pid) {
    err.value = '请选择圈舍';
    return;
  }
  if (!eff) {
    err.value = '请填写编号前缀';
    return;
  }
  if (cnt < 1 || cnt > 200) {
    err.value = '批量数量需在 1~200 之间';
    return;
  }
  if (pen && cnt > pen.diff) {
    err.value = `「${pen.pen_name}」可补额度仅 ${pen.diff} 头；新入栏动物请先走「调整存栏」流水`;
    return;
  }
  loading.value = true;
  try {
    const r = await apiBatchCreateIndividuals({
      pen_id: pid,
      gender: genderVal,
      prefix: eff,
      start_no: start,
      count: cnt,
      birth_date: birth || undefined,
    });
    emit('saved', `已批量建档 ${r.created.length} 个（${r.tag_nos[0]} ~ ${r.tag_nos[r.tag_nos.length - 1]}）`);
  } catch (e) {
    err.value = e instanceof ApiError ? `${e.message}（已建档部分保持有效，修正后可从断点续建）` : '网络异常，请重试';
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <label for="bat-pen" class="text-[13px] text-text-secondary mb-1.5 block">
    圈舍 <span style="color: var(--danger)">*</span>
  </label>
  <PenSelect v-model:value="penId" :pens="pens" id="bat-pen" />

  <div class="grid grid-cols-3 gap-3 mb-4">
    <div>
      <label for="bat-gender" class="text-[13px] text-text-secondary mb-1.5 block">性别 *</label>
      <select id="bat-gender" v-model="gender" :class="inputCls">
        <option value="male">公</option>
        <option value="female">母</option>
      </select>
    </div>
    <div>
      <label for="bat-prefix" class="text-[13px] text-text-secondary mb-1.5 block">编号前缀</label>
      <input
        id="bat-prefix"
        v-model="prefix"
        maxlength="20"
        :placeholder="penId ? `P${penId}` : '如 P1'"
        :class="inputCls"
      />
    </div>
    <div>
      <label for="bat-start" class="text-[13px] text-text-secondary mb-1.5 block">起始序号</label>
      <input
        id="bat-start"
        type="number"
        min="1"
        :value="startNo"
        :class="inputCls"
        @input="handleStartInput"
      />
    </div>
  </div>

  <div class="grid grid-cols-2 gap-3 mb-4">
    <div>
      <label for="bat-count" class="text-[13px] text-text-secondary mb-1.5 block">
        数量 <span class="text-text-tertiary">{{ selPen ? `（可补 ${headroom}）` : '' }}</span>
      </label>
      <input
        id="bat-count"
        type="number"
        min="1"
        max="200"
        :value="count"
        :class="inputCls"
        @input="handleCountInput"
      />
    </div>
    <div>
      <label for="bat-birth" class="text-[13px] text-text-secondary mb-1.5 block">
        出生日期 <span class="text-text-tertiary">（选填）</span>
      </label>
      <input id="bat-birth" v-model="birthDate" type="date" :class="inputCls" />
    </div>
  </div>

  <p v-if="preview" class="text-[12px] text-text-tertiary mb-4 tnum break-all">
    生成预览：{{ preview }}
  </p>

  <FormError :err="err" />
  <ModalActions :loading="loading" submit-text="批量建档" @cancel="emit('close')" @submit="submit" />
</template>
