<script setup lang="ts">
import { computed, type Component } from 'vue';
import { AlertTriangle, CheckCircle2, ShieldAlert } from 'lucide-vue-next';

const props = defineProps<{ risk: string; unresolved?: boolean }>();

interface RiskCfg {
  label: string;
  color: string;
  bg: string;
  icon: Component;
}

// 风险标签语义色（06 规范 4.9：low 绿/mid 橙/high 红"已转交兽医复核"）
const RISK_STYLE: Record<string, RiskCfg> = {
  low: { label: '低风险', color: 'var(--success)', bg: 'rgba(46,158,107,.08)', icon: CheckCircle2 },
  mid: { label: '中度风险', color: 'var(--warning)', bg: 'rgba(232,162,61,.09)', icon: AlertTriangle },
  high: { label: '已转交兽医复核', color: 'var(--danger)', bg: 'rgba(220,80,80,.08)', icon: ShieldAlert },
};

const cfg = computed(() => RISK_STYLE[props.risk] ?? null);
</script>

<template>
  <div v-if="cfg" class="mb-2">
    <span
      class="inline-flex items-center gap-1 px-2 py-0.5 rounded-[6px] text-[12px] font-medium"
      :style="{ backgroundColor: cfg.bg, color: cfg.color }"
    >
      <component :is="cfg.icon" :size="12" :stroke-width="1.8" />
      {{ unresolved && risk === 'high' ? '高风险 · 定位圈舍后转交兽医复核' : cfg.label }}
    </span>
  </div>
</template>
