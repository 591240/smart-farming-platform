<script setup lang="ts">
import { computed } from 'vue';
import type { WeightPoint } from '@/api/individual-api';

const props = defineProps<{ series: WeightPoint[] }>();

const W = 480;
const H = 120;
const pad = 8;

interface ChartPoint extends WeightPoint {
  x: number;
  y: number;
}

const chart = computed(() => {
  if (props.series.length === 0) return null;
  const ks = props.series.map((s) => s.weight_kg);
  const min = Math.min(...ks);
  const max = Math.max(...ks);
  const span = Math.max(max - min, 1);
  const pts: ChartPoint[] = props.series.map((s, i) => ({
    x: pad + (i * (W - pad * 2)) / Math.max(props.series.length - 1, 1),
    y: H - pad - ((s.weight_kg - min) / span) * (H - pad * 2),
    ...s,
  }));
  const latest = props.series[props.series.length - 1];
  const gains = props.series.map((s) => s.daily_gain).filter((g): g is number => g !== null);
  const avgGain = gains.length > 0 ? (gains.reduce((a, b) => a + b, 0) / gains.length).toFixed(2) : null;
  return { pts, latest, avgGain };
});

function pointTitle(p: ChartPoint): string {
  return `${p.date}：${p.weight_kg} kg${p.daily_gain !== null ? `（日增重 ${p.daily_gain} kg）` : ''}`;
}
</script>

<template>
  <p v-if="!chart" class="text-[12px] text-text-tertiary py-3">
    还没有体重记录，点「记一笔 → 体重」后生成生长曲线。
  </p>
  <div v-else>
    <svg :viewBox="`0 0 ${W} ${H}`" class="w-full" role="img" aria-label="体重曲线">
      <polyline
        :points="chart.pts.map((p) => `${p.x},${p.y}`).join(' ')"
        fill="none"
        stroke="var(--primary, #3a6b4f)"
        stroke-width="2"
        stroke-linejoin="round"
      />
      <g v-for="(p, i) in chart.pts" :key="i">
        <circle :cx="p.x" :cy="p.y" r="3" fill="var(--primary, #3a6b4f)" />
        <title>{{ pointTitle(p) }}</title>
      </g>
    </svg>
    <p class="text-[12px] text-text-tertiary mt-1 tnum">
      最新 <b class="text-text-secondary">{{ chart.latest.weight_kg }} kg</b>（{{ chart.latest.date }}）<template v-if="chart.avgGain !== null"> · 平均日增重 {{ chart.avgGain }} kg/日</template>
    </p>
  </div>
</template>
