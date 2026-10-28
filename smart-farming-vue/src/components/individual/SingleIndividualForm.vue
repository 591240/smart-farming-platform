<script setup lang="ts">
import { ref } from 'vue';
import { cn } from '@/lib/utils';
import { ApiError } from '@/lib/api-contract';
import { apiCreateIndividual, type IndividualGender, type PenReconcileItem } from '@/api/individual-api';
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
const tagNo = ref('');
const gender = ref<IndividualGender | ''>('');
const birthDate = ref('');
const source = ref('');
const remark = ref('');
const err = ref<string | null>(null);
const loading = ref(false);

const genderOptions: { v: IndividualGender; label: string }[] = [
  { v: 'male', label: '公' },
  { v: 'female', label: '母' },
];

async function submit() {
  err.value = null;
  if (!penId.value) {
    err.value = '请选择圈舍';
    return;
  }
  if (!tagNo.value.trim()) {
    err.value = '请填写耳标号';
    return;
  }
  if (!gender.value) {
    err.value = '请选择性别';
    return;
  }
  const pid = penId.value;
  const tag = tagNo.value.trim();
  const genderVal = gender.value;
  const birth = birthDate.value;
  const src = source.value.trim();
  const remarkVal = remark.value.trim();
  loading.value = true;
  try {
    await apiCreateIndividual({
      pen_id: pid,
      ear_tag_no: tag,
      gender: genderVal,
      birth_date: birth || undefined,
      source: src || undefined,
      remark: remarkVal || undefined,
    });
    emit('saved', `耳标 ${tag} 已建档`);
  } catch (e) {
    err.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <label for="ind-pen" class="text-[13px] text-text-secondary mb-1.5 block">
    圈舍 <span style="color: var(--danger)">*</span>
  </label>
  <PenSelect v-model:value="penId" :pens="pens" id="ind-pen" />

  <div class="grid grid-cols-2 gap-3 mb-4">
    <div>
      <label for="ind-tag" class="text-[13px] text-text-secondary mb-1.5 block">
        耳标号 <span style="color: var(--danger)">*</span>
      </label>
      <input id="ind-tag" v-model="tagNo" maxlength="32" placeholder="如 P1-M016" :class="inputCls" />
    </div>
    <div>
      <label for="ind-birth" class="text-[13px] text-text-secondary mb-1.5 block">
        出生日期 <span class="text-text-tertiary">（选填）</span>
      </label>
      <input id="ind-birth" v-model="birthDate" type="date" :class="inputCls" />
    </div>
  </div>

  <label class="text-[13px] text-text-secondary mb-1.5 block">
    性别 <span style="color: var(--danger)">*</span>
  </label>
  <div class="flex gap-2 mb-4">
    <button
      v-for="o in genderOptions"
      :key="o.v"
      type="button"
      :class="
        cn(
          'flex-1 py-2 rounded-[10px] text-[13px] font-medium border transition-all duration-[var(--duration-fast)]',
          gender === o.v
            ? 'bg-primary-50 text-primary-700 border-primary'
            : 'bg-bg-subtle text-text-secondary border-transparent hover:bg-bg-inset',
        )
      "
      @click="gender = o.v"
    >
      {{ o.label }}
    </button>
  </div>

  <div class="grid grid-cols-2 gap-3 mb-4">
    <div>
      <label for="ind-source" class="text-[13px] text-text-secondary mb-1.5 block">
        来源 <span class="text-text-tertiary">（选填）</span>
      </label>
      <input id="ind-source" v-model="source" maxlength="20" placeholder="默认：人工建档" :class="inputCls" />
    </div>
    <div>
      <label for="ind-remark" class="text-[13px] text-text-secondary mb-1.5 block">
        备注 <span class="text-text-tertiary">（选填）</span>
      </label>
      <input id="ind-remark" v-model="remark" maxlength="60" :class="inputCls" />
    </div>
  </div>

  <FormError :err="err" />
  <ModalActions :loading="loading" submit-text="确认建档" @cancel="emit('close')" @submit="submit" />
</template>
