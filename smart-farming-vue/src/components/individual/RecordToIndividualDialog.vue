<script setup lang="ts">
// 记入个体档案 — 告警卡与 AI 问诊结果卡共用同一个写档入口（F14 个体绑定）
// 两处共用而不是各写一份：告警与问诊只是"这头动物发病了"的两个来源，
// 后端 treatment 事件契约只有一份，两个弹窗等于两套字段名、两套校验。
// 耳标搜索走服务端 keyword（GET /individual/list）：演示账号几百头，前端不全量拉回再 filter。
/** 写档：把告警/问诊结论记为该个体的发病诊疗事件（后端 treatment） */
import { ref } from 'vue';
import { cn, todayLocal } from '@/lib/utils';
import { ApiError } from '@/lib/api-contract';
import { apiCreateIndividualEvent } from '@/api/individual-api';
import { fieldCls, inputCls } from './styles';
import type { PickedIndividual } from './types';
import Modal from './Modal.vue';
import IndividualPicker from './IndividualPicker.vue';
import FormError from './FormError.vue';
import ModalActions from './ModalActions.vue';

const props = defineProps<{
  prefill?: { symptom?: string; diagnosis?: string; medication?: string };
  /** 本轮问诊已绑定的个体：省得再搜一遍耳标（可换） */
  defaultPick?: PickedIndividual | null;
}>();

const emit = defineEmits<{ close: []; saved: [picked: PickedIndividual] }>();

const sel = ref<PickedIndividual | null>(props.defaultPick ?? null);
const eventDate = ref(todayLocal());
const symptom = ref((props.prefill?.symptom ?? '').slice(0, 128));
const diagnosis = ref((props.prefill?.diagnosis ?? '').slice(0, 128));
const medication = ref((props.prefill?.medication ?? '').slice(0, 128));
const outcome = ref('');
const err = ref<string | null>(null);
const loading = ref(false);

async function submit() {
  err.value = null;
  if (!sel.value) {
    err.value = '请先选择个体（耳标）';
    return;
  }
  if (!symptom.value.trim()) {
    err.value = '请填写症状描述';
    return;
  }
  const picked = sel.value;
  const date = eventDate.value;
  const symptomVal = symptom.value.trim();
  const diagnosisVal = diagnosis.value.trim();
  const medicationVal = medication.value.trim();
  const outcomeVal = outcome.value.trim();
  loading.value = true;
  try {
    await apiCreateIndividualEvent(picked.individual_id, {
      event_type: 'treatment',
      event_date: date,
      symptom: symptomVal,
      ...(diagnosisVal ? { diagnosis: diagnosisVal } : {}),
      ...(medicationVal ? { medication: medicationVal } : {}),
      ...(outcomeVal ? { outcome: outcomeVal } : {}),
    });
    emit('saved', picked);
  } catch (e) {
    err.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <Modal
    title="记入个体档案"
    subtitle="写入该个体时间线的「发病/诊疗」事件，与问诊、告警互相独立"
    subtitle-class="mb-4"
    @close="emit('close')"
  >
    <IndividualPicker v-model:value="sel" />

    <label for="rec-date" :class="fieldCls">
      日期 <span style="color: var(--danger)">*</span>
    </label>
    <input
      id="rec-date"
      v-model="eventDate"
      type="date"
      :max="todayLocal()"
      :class="cn(inputCls, 'mb-4')"
    />

    <label for="rec-symptom" :class="fieldCls">
      症状 <span style="color: var(--danger)">*</span>
    </label>
    <textarea
      id="rec-symptom"
      v-model="symptom"
      maxlength="128"
      rows="2"
      :class="cn(inputCls, 'h-auto py-2 resize-none mb-4')"
    />

    <div class="grid grid-cols-2 gap-3 mb-4">
      <div>
        <label for="rec-dx" :class="fieldCls">诊断 <span class="text-text-tertiary">（选填）</span></label>
        <input id="rec-dx" v-model="diagnosis" maxlength="128" :class="inputCls" />
      </div>
      <div>
        <label for="rec-rx" :class="fieldCls">用药 <span class="text-text-tertiary">（选填）</span></label>
        <input id="rec-rx" v-model="medication" maxlength="128" :class="inputCls" />
      </div>
    </div>
    <label for="rec-outcome" :class="fieldCls">
      处置结果 <span class="text-text-tertiary">（选填，如：已隔离并投药）</span>
    </label>
    <input id="rec-outcome" v-model="outcome" maxlength="64" :class="cn(inputCls, 'mb-5')" />

    <FormError :err="err" />
    <ModalActions
      :loading="loading"
      submit-text="写入档案"
      inactive-style="muted"
      @cancel="emit('close')"
      @submit="submit"
    />
  </Modal>
</template>
