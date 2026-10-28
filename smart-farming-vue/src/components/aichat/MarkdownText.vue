<script setup lang="ts">
// ===== 简易 Markdown 渲染（加粗 + 列表；输出为纯文本段落，无 HTML 注入面）=====
import { computed } from 'vue';

const props = defineProps<{ text: string }>();

interface MdLine {
  key: number;
  bullet: boolean;
  /** 含 ** 时按捕获组切分；null 表示整行纯文本 */
  parts: string[] | null;
  text: string;
}

const lines = computed<MdLine[]>(() =>
  props.text.split('\n').map((line, i) => ({
    key: i,
    bullet: line.startsWith('- '),
    parts: line.includes('**') ? line.split(/\*\*(.*?)\*\*/g) : null,
    text: line,
  })),
);
</script>

<template>
  <template v-for="(ln, i) in lines" :key="ln.key">
    <div v-if="ln.bullet" class="flex items-start gap-2 ml-2 my-0.5">
      <span class="w-1 h-1 rounded-full bg-text-tertiary mt-[9px] shrink-0" />
      <span>
        <template v-if="ln.parts">
          <template v-for="(part, j) in ln.parts" :key="j">
            <strong v-if="j % 2 === 1" class="font-medium text-text-primary">{{ part }}</strong>
            <template v-else>{{ part }}</template>
          </template>
        </template>
        <template v-else>{{ ln.text.slice(2) }}</template>
      </span>
    </div>
    <span v-else>
      <template v-if="ln.parts">
        <template v-for="(part, j) in ln.parts" :key="j">
          <strong v-if="j % 2 === 1" class="font-medium text-text-primary">{{ part }}</strong>
          <template v-else>{{ part }}</template>
        </template>
      </template>
      <template v-else>{{ ln.text }}</template>
      <br v-if="i < lines.length - 1" />
    </span>
  </template>
</template>
