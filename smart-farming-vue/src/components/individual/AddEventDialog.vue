<script setup lang="ts">
// ===== 记一笔弹窗（体重/免疫/诊疗/备注四类型）=====
import { ref } from 'vue';
import { cn, todayLocal } from '@/lib/utils';
import { ApiError } from '@/lib/api-contract';
import { apiCreateIndividualEvent, type IndividualEventPayload } from '@/api/individual-api';
import { inputCls } from './styles';
import Modal from './Modal.vue';
import FormError from './FormError.vue';
import ModalActions from './ModalActions.vue';

const props = defineProps<{
  individualId: number;
  earTagNo: string;
  birthDate: string | null;
}>();

const emit = defineEmits<{ close: []; saved: [] }>();

type ManualEventType = 'weight' | 'immunization' | 'treatment' | 'note';

const type = ref<ManualEventType>('weight');
const eventDate = ref(todayLocal());
const weightKg = ref('');
const vaccineName = ref('');
const dose = ref('');
const nextDate = ref('');
const symptom = ref('');
const diagnosis = ref('');
const medication = ref('');
const outcome = ref('');
const vetName = ref('');
const content = ref('');
const note = ref('');
const err = ref<string | null>(null);
const loading = ref(false);

const typeTabs: { v: ManualEventType; label: string }[] = [
  { v: 'weight', label: '体重' },
  { v: 'immunization', label: '免疫/驱虫' },
  { v: 'treatment', label: '发病/诊疗' },
  { v: 'note', label: '备注' },
];

const field = 'text-[13px] text-text-secondary mb-1.5 block';

async function submit() {
  err.value = null;
  const typeVal = type.value;
  const date = eventDate.value;
  const birthErr = props.birthDate && date < props.birthDate ? '事件日期不能早于出生日期' : null;
  if (birthErr) {
    err.value = birthErr;
    return;
  }
  const payload: IndividualEventPayload = { event_type: typeVal, event_date: date };
  if (typeVal === 'weight') {
    const kg = Number(weightKg.value);
    if (!Number.isFinite(kg) || kg <= 0) {
      err.value = '请填写正确体重';
      return;
    }
    payload.weight_kg = kg;
    if (note.value.trim()) payload.note = note.value.trim();
  } else if (typeVal === 'immunization') {
    if (!vaccineName.value.trim()) {
      err.value = '请填写疫苗/驱虫药名称';
      return;
    }
    payload.vaccine_name = vaccineName.value.trim();
    if (dose.value.trim()) payload.dose = dose.value.trim();
    if (nextDate.value) payload.next_date = nextDate.value;
    if (note.value.trim()) payload.note = note.value.trim();
  } else if (typeVal === 'treatment') {
    if (!symptom.value.trim()) {
      err.value = '请填写症状描述';
      return;
    }
    payload.symptom = symptom.value.trim();
    if (diagnosis.value.trim()) payload.diagnosis = diagnosis.value.trim();
    if (medication.value.trim()) payload.medication = medication.value.trim();
    if (outcome.value.trim()) payload.outcome = outcome.value.trim();
    if (vetName.value.trim()) payload.vet_name = vetName.value.trim();
  } else {
    if (!content.value.trim()) {
      err.value = '请填写备注内容';
      return;
    }
    payload.content = content.value.trim();
  }
  loading.value = true;
  try {
    await apiCreateIndividualEvent(props.individualId, payload);
    emit('saved');
  } catch (e) {
    err.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <Modal title="记一笔" :subtitle="`${earTagNo} · 写入个体档案时间线`" @close="emit('close')">
    <div class="grid grid-cols-4 gap-2 mb-4">
      <button
        v-for="t in typeTabs"
        :key="t.v"
        type="button"
        :class="
          cn(
            'py-2 rounded-[10px] text-[13px] font-medium border transition-all duration-[var(--duration-fast)]',
            type === t.v
              ? 'bg-primary-50 text-primary-700 border-primary'
              : 'bg-bg-subtle text-text-secondary border-transparent hover:bg-bg-inset',
          )
        "
        @click="type = t.v"
      >
        {{ t.label }}
      </button>
    </div>

    <label for="ev-date" :class="field">
      日期 <span style="color: var(--danger)">*</span>
      <span v-if="birthDate" class="text-text-tertiary">（不得早于出生 {{ birthDate }}）</span>
    </label>
    <input
      id="ev-date"
      v-model="eventDate"
      type="date"
      :max="todayLocal()"
      :class="cn(inputCls, 'mb-4')"
    />

    <template v-if="type === 'weight'">
      <label for="ev-weight" :class="field">
        体重（kg） <span style="color: var(--danger)">*</span>
      </label>
      <input
        id="ev-weight"
        v-model="weightKg"
        type="number"
        min="0.1"
        max="500"
        step="0.1"
        :class="cn(inputCls, 'mb-4')"
      />
      <label for="ev-note" :class="field">备注 <span class="text-text-tertiary">（选填）</span></label>
      <input id="ev-note" v-model="note" maxlength="128" :class="cn(inputCls, 'mb-4')" />
    </template>

    <template v-if="type === 'immunization'">
      <label for="ev-vaccine" :class="field">
        疫苗/驱虫药名称 <span style="color: var(--danger)">*</span>
      </label>
      <input
        id="ev-vaccine"
        v-model="vaccineName"
        maxlength="64"
        placeholder="如：口蹄疫灭活疫苗"
        :class="cn(inputCls, 'mb-4')"
      />
      <div class="grid grid-cols-2 gap-3 mb-4">
        <div>
          <label for="ev-dose" :class="field">剂量 <span class="text-text-tertiary">（选填）</span></label>
          <input id="ev-dose" v-model="dose" maxlength="64" placeholder="如：2ml 肌注" :class="inputCls" />
        </div>
        <div>
          <label for="ev-next" :class="field">下次免疫日 <span class="text-text-tertiary">（选填）</span></label>
          <input id="ev-next" v-model="nextDate" type="date" :class="inputCls" />
        </div>
      </div>
      <label for="ev-inote" :class="field">备注 <span class="text-text-tertiary">（选填）</span></label>
      <input id="ev-inote" v-model="note" maxlength="128" :class="cn(inputCls, 'mb-4')" />
    </template>

    <template v-if="type === 'treatment'">
      <label for="ev-symptom" :class="field">
        症状描述 <span style="color: var(--danger)">*</span>
      </label>
      <input
        id="ev-symptom"
        v-model="symptom"
        maxlength="128"
        placeholder="如：咳嗽、流涕、采食下降"
        :class="cn(inputCls, 'mb-4')"
      />
      <div class="grid grid-cols-2 gap-3 mb-4">
        <div>
          <label for="ev-diag" :class="field">诊断 <span class="text-text-tertiary">（选填）</span></label>
          <input id="ev-diag" v-model="diagnosis" maxlength="128" :class="inputCls" />
        </div>
        <div>
          <label for="ev-med" :class="field">用药 <span class="text-text-tertiary">（选填）</span></label>
          <input id="ev-med" v-model="medication" maxlength="128" :class="inputCls" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-3 mb-4">
        <div>
          <label for="ev-outcome" :class="field">诊疗结果 <span class="text-text-tertiary">（选填）</span></label>
          <input id="ev-outcome" v-model="outcome" maxlength="64" placeholder="如：康复" :class="inputCls" />
        </div>
        <div>
          <label for="ev-vet" :class="field">诊疗人员 <span class="text-text-tertiary">（选填）</span></label>
          <input id="ev-vet" v-model="vetName" maxlength="32" :class="inputCls" />
        </div>
      </div>
    </template>

    <template v-if="type === 'note'">
      <label for="ev-content" :class="field">
        备注内容 <span style="color: var(--danger)">*</span>
      </label>
      <input id="ev-content" v-model="content" maxlength="128" :class="cn(inputCls, 'mb-4')" />
    </template>

    <FormError :err="err" />
    <ModalActions :loading="loading" submit-text="写入档案" @cancel="emit('close')" @submit="submit" />
  </Modal>
</template>
