<script setup lang="ts">
/** 问诊输入区用：只选人，不写内容（选定后 individual_id 随本轮消息一起发给后端） */
import { ref } from 'vue';
import Modal from './Modal.vue';
import IndividualPicker from './IndividualPicker.vue';
import ModalActions from './ModalActions.vue';
import type { PickedIndividual } from './types';

const emit = defineEmits<{ close: []; pick: [v: PickedIndividual] }>();

const sel = ref<PickedIndividual | null>(null);

function submit() {
  if (sel.value) emit('pick', sel.value);
}
</script>

<template>
  <Modal
    title="绑定个体"
    subtitle="本轮问诊挂到这一头，并按它所在圈舍定位（不选则按整圈口径作答）"
    subtitle-class="mb-4"
    @close="emit('close')"
  >
    <IndividualPicker v-model:value="sel" />
    <ModalActions
      :loading="false"
      submit-text="确定绑定"
      :disabled="!sel"
      inactive-style="muted"
      @cancel="emit('close')"
      @submit="submit"
    />
  </Modal>
</template>
