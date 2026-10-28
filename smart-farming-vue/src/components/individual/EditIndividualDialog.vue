<script setup lang="ts">
// ===== 编辑弹窗 =====
import { ref } from 'vue';
import { cn } from '@/lib/utils';
import { ApiError } from '@/lib/api-contract';
import { apiUpdateIndividual, type IndividualItem } from '@/api/individual-api';
import { inputCls } from './styles';
import Modal from './Modal.vue';
import FormError from './FormError.vue';
import ModalActions from './ModalActions.vue';

const props = defineProps<{ item: IndividualItem }>();

const emit = defineEmits<{ close: []; saved: [] }>();

const tagNo = ref(props.item.ear_tag_no);
const birthDate = ref(props.item.birth_date ?? '');
const source = ref(props.item.source);
const remark = ref(props.item.remark);
const traits = ref(props.item.traits ?? '');
const lineage = ref(props.item.lineage ?? '');
const err = ref<string | null>(null);
const loading = ref(false);

async function submit() {
  err.value = null;
  if (!tagNo.value.trim()) {
    err.value = '请填写耳标号';
    return;
  }
  const tag = tagNo.value.trim();
  const birth = birthDate.value;
  const src = source.value.trim();
  const remarkVal = remark.value.trim();
  const traitsVal = traits.value.trim();
  const lineageVal = lineage.value.trim();
  loading.value = true;
  try {
    await apiUpdateIndividual(props.item.individual_id, {
      ear_tag_no: tag,
      birth_date: birth || null,
      source: src,
      remark: remarkVal,
      traits: traitsVal,
      lineage: lineageVal,
    });
    emit('saved');
  } catch (e) {
    err.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <Modal
    title="编辑个体档案"
    :subtitle="`${item.ear_tag_no} · ${item.pen_name} · ${item.gender_label} · ${item.status_label}`"
    @close="emit('close')"
  >
    <label for="ed-tag" class="text-[13px] text-text-secondary mb-1.5 block">
      耳标号 <span style="color: var(--danger)">*</span>
      <span class="text-text-tertiary">（换号将自动留档一条「换标」记录）</span>
    </label>
    <input id="ed-tag" v-model="tagNo" maxlength="32" :class="cn(inputCls, 'mb-4')" />

    <div class="grid grid-cols-2 gap-3 mb-4">
      <div>
        <label for="ed-birth" class="text-[13px] text-text-secondary mb-1.5 block">出生日期</label>
        <input id="ed-birth" v-model="birthDate" type="date" :class="inputCls" />
      </div>
      <div>
        <label for="ed-source" class="text-[13px] text-text-secondary mb-1.5 block">来源</label>
        <input id="ed-source" v-model="source" maxlength="20" :class="inputCls" />
      </div>
    </div>

    <label for="ed-traits" class="text-[13px] text-text-secondary mb-1.5 block">
      外貌/毛色特征 <span class="text-text-tertiary">（选填，如：白底黑斑、左耳缺刻）</span>
    </label>
    <input id="ed-traits" v-model="traits" maxlength="128" :class="cn(inputCls, 'mb-4')" />

    <label for="ed-lineage" class="text-[13px] text-text-secondary mb-1.5 block">
      血缘备注 <span class="text-text-tertiary">（选填，种畜记录父系/母系品种与母本耳标号）</span>
    </label>
    <input id="ed-lineage" v-model="lineage" maxlength="128" :class="cn(inputCls, 'mb-4')" />

    <label for="ed-remark" class="text-[13px] text-text-secondary mb-1.5 block">备注</label>
    <input id="ed-remark" v-model="remark" maxlength="60" :class="cn(inputCls, 'mb-4')" />

    <p v-if="item.status !== 'active' && item.exit_note" class="text-[12px] text-text-tertiary mb-4">
      离栏记录：{{ item.exit_note }}（{{ item.exit_time?.slice(0, 10) }}）
    </p>

    <FormError :err="err" />
    <ModalActions :loading="loading" submit-text="保存修改" @cancel="emit('close')" @submit="submit" />
  </Modal>
</template>
