<!-- 畜牧养殖综合展示 3D — 毕业设计演示页（路由 /showcase3d）
     套 LayoutShell 壳（登录守卫 + 侧边栏复用），Three.js 场景见 components/showcase3d/BarnShowcase
     物种来源：用户实际圈舍的 species_code 去重（有啥动物展示啥栏舍；无数据时兜底三种可选）
     F06 联动：告警中心「3D 定位」带 search 参数跳入 → 按圈舍物种切舍 + 锚点闪红定位
     F15 拍照建场景：上传场舍照片 → 识别（mock/qwen 多模态）→ 参数化生成定制 3D 场景 -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useQuery } from '@tanstack/vue-query';
import { Camera, RotateCcw, Warehouse, X } from 'lucide-vue-next';
import { apiListFarms, apiListPens } from '@/api/auth-api';
import { apiAnalyzeScenePhoto, apiSceneLatest, type SceneAnalyzeResult } from '@/api/scene-api';
import BarnShowcase from '@/components/showcase3d/BarnShowcase.vue';
import type { BarnFocus, BarnInstance, HotspotKey, Species } from '@/components/showcase3d/types';

const ALERT_TYPE_LABEL: Record<string, string> = {
  sensor: '传感器告警', climate: '气象告警', vision: '视觉告警',
  disease: '疫病告警', device_offline: '设备离线',
};

/** 圈舍物种判定：圈舍名关键词优先（母猪圈→猪，防 species_code 与名称不符），否则用 species_code */
function resolvePenSpecies(penName: string, code: string): Species {
  if (penName.includes('猪')) return 'pig';
  if (penName.includes('牛')) return 'cattle';
  if (penName.includes('羊')) return 'sheep';
  return (code as Species) || 'pig';
}

const route = useRoute();
const router = useRouter();

const q = (v: unknown) => (typeof v === 'string' ? v : undefined);
const alertType = computed(() => q(route.query.alert_type));
const penName = computed(() => q(route.query.pen_name));
const content = computed(() => q(route.query.content));

const farmsQ = useQuery({ queryKey: computed(() => ['farms']), queryFn: apiListFarms });
const farmIds = computed(() => (farmsQ.data.value?.items ?? []).map((f) => f.farm_id));

const pensQ = useQuery({
  queryKey: computed(() => ['pens-showcase', farmIds.value.join(',')]),
  queryFn: async () => {
    const res = await Promise.all(farmIds.value.map((id) => apiListPens(id)));
    return res.flatMap((r) => r.items);
  },
  enabled: computed(() => farmIds.value.length > 0),
});

const loading = computed(() => farmsQ.isLoading.value || (farmIds.value.length > 0 && pensQ.isLoading.value));

// 圈舍列表（每项=一栋）：物种按圈舍名关键词判定（母猪圈→猪舍），兜底 species_code；count/male/female=真实存栏
const barns = computed<BarnInstance[] | undefined>(() => {
  if (!pensQ.data.value) return undefined;
  return pensQ.data.value.map((p) => ({
    name: p.pen_name,
    species: resolvePenSpecies(p.pen_name, p.species_code),
    count: (p.male_count ?? 0) + (p.female_count ?? 0),
    male: p.male_count ?? 0,
    female: p.female_count ?? 0,
  }));
});

// 告警定位：alert_type → 场景锚点（环控/设备/监控/栏位）；物种由定位组件按圈舍列表解析
const hotspot = computed<HotspotKey | undefined>(() =>
  alertType.value
    ? ({ sensor: 'env', climate: 'env', device_offline: 'device', vision: 'cam', disease: 'pen' } as Record<string, HotspotKey>)[alertType.value]
    : undefined,
);
const focusSpecies = computed<Species | undefined>(() => {
  const list = barns.value;
  return penName.value
    ? (list?.find((b) => b.name === penName.value)?.species ?? list?.[0]?.species)
    : list?.[0]?.species;
});
const focus = computed<BarnFocus | null>(() =>
  focusSpecies.value && hotspot.value
    ? { species: focusSpecies.value, hotspot: hotspot.value, penName: penName.value ?? undefined }
    : null,
);

// ----- F15 拍照建场景 -----
const dlgOpen = ref(false);
const previews = ref<string[]>([]);
const busy = ref(false);
const err = ref<string | null>(null);
const photo = ref<SceneAnalyzeResult | null>(null);
// 非 null = 这条场景是从后端恢复的「上次识别结果」，不是本次刚算的（横幅要如实标注）
const photoRestoredAt = ref<string | null>(null);
const demoPreview = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);

const savedScene = useQuery({ queryKey: computed(() => ['scene-latest']), queryFn: apiSceneLatest });
const sceneDismissed = ref(false);
watch(
  [() => savedScene.data.value, photo, sceneDismissed],
  () => {
    const s = savedScene.data.value;
    if (!s?.has_scene || !s.config || photo.value || sceneDismissed.value) return;
    photo.value = { config: s.config, summary: s.summary ?? '', confidence: s.confidence ?? 0 };
    photoRestoredAt.value = s.create_time ?? '';
  },
  { immediate: true },
);

// 空状态：未建圈舍时不渲染 3D 场景（示例预览除外）
const hasBarns = computed(() => !!barns.value && barns.value.length > 0);
const showScene = computed(() => hasBarns.value || demoPreview.value);
// 示例预览：无圈舍时可看标准场景模板
const sceneBarns = computed<BarnInstance[] | undefined>(() =>
  hasBarns.value
    ? barns.value
    : (demoPreview.value ? [{ name: '示例 · 生猪栏舍', species: 'pig' }] : undefined),
);

const totals = computed(() => {
  const list = barns.value ?? [];
  const tm = list.reduce((s, b) => s + (b.male ?? 0), 0);
  const tf = list.reduce((s, b) => s + (b.female ?? 0), 0);
  return { male: tm, female: tf, all: tm + tf };
});

function onFiles(e: Event): void {
  const files = Array.from((e.target as HTMLInputElement).files ?? []);
  err.value = null;
  Promise.all(files.map((f) => new Promise<string>((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(new Error('读取失败'));
    r.readAsDataURL(f);
  })))
    .then((v) => { previews.value = v; })
    .catch(() => { err.value = '照片读取失败，请重试'; });
}

async function doAnalyze(): Promise<void> {
  busy.value = true; err.value = null;
  try {
    const res = await apiAnalyzeScenePhoto(previews.value);
    photo.value = res;
    photoRestoredAt.value = null;
    sceneDismissed.value = false;
    dlgOpen.value = false;
  } catch (e) {
    err.value = e instanceof Error ? e.message : '识别失败，请重试';
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="relative h-full">
    <div v-if="loading" class="h-full flex items-center justify-center text-[14px] text-text-tertiary">
      正在加载栏舍数据…
    </div>
    <BarnShowcase
      v-else-if="showScene"
      :barns="sceneBarns"
      :focus="focus"
      :photo-config="photo?.config ?? null"
    />
    <!-- 空状态：未创建圈舍时的引导 -->
    <div v-else class="h-full flex flex-col items-center justify-center gap-3">
      <div class="w-14 h-14 rounded-2xl bg-primary-50 flex items-center justify-center">
        <Warehouse :size="26" :stroke-width="1.5" class="text-primary-700" />
      </div>
      <p class="text-[16px] font-medium text-text-primary mt-1">还没有创建圈舍</p>
      <p class="text-[13px] text-text-tertiary">
        创建圈舍后，这里会自动生成你家场舍的三维场景
      </p>
      <div class="flex items-center gap-2 mt-2">
        <button
          type="button"
          @click="router.push('/farm')"
          class="px-4 py-2 rounded-lg text-[13px] text-white bg-primary hover:bg-primary-600 font-medium transition-colors"
        >
          去创建圈舍
        </button>
        <button
          type="button"
          @click="demoPreview = true"
          class="px-4 py-2 rounded-lg text-[13px] border border-border text-text-secondary bg-white/90 hover:bg-white transition-colors"
        >
          先看示例场景
        </button>
      </div>
    </div>

    <!-- F15 入口按钮（有场景时才显示） -->
    <div v-if="showScene" class="absolute left-4 top-16 z-20">
      <button
        type="button"
        @click="dlgOpen = true; err = null"
        class="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-[13px] border border-border bg-white/90 text-text-primary hover:bg-white transition-colors shadow-sm"
      >
        <Camera :size="14" :stroke-width="1.5" />
        拍照建场景
      </button>
    </div>

    <!-- 示例预览提示条 -->
    <div
      v-if="demoPreview && !hasBarns"
      class="absolute left-1/2 top-16 -translate-x-1/2 z-20 flex items-center gap-3 max-w-[520px] rounded-xl border border-border bg-white/92 px-4 py-2.5 shadow-sm"
    >
      <span class="w-2.5 h-2.5 rounded-full bg-[#f0b429] shrink-0" />
      <p class="text-[13px] text-text-primary">
        当前为<strong>示例场景</strong>，创建圈舍后将自动替换为你自己的场舍
      </p>
      <button
        type="button"
        @click="demoPreview = false"
        class="shrink-0 px-3 py-1 rounded-lg text-[12px] border border-border text-text-secondary hover:bg-bg-subtle transition-colors"
      >
        退出示例
      </button>
    </div>

    <!-- 存栏统计面板（公/母/合计，数据与总览看板同源） -->
    <div
      v-if="hasBarns && barns"
      class="absolute right-4 top-4 z-20 w-[236px] rounded-xl border border-border bg-white/85 backdrop-blur px-3.5 py-2.5 text-[12px] shadow-sm"
    >
      <div class="text-[13px] font-semibold text-text-primary mb-1.5">存栏统计</div>
      <div class="grid grid-cols-[1fr_34px_34px_40px] gap-x-1 gap-y-0.5 items-center max-h-[38vh] overflow-y-auto pr-0.5">
        <span class="text-[11px] text-text-tertiary">圈舍</span>
        <span class="text-[11px] text-text-tertiary text-right">公</span>
        <span class="text-[11px] text-text-tertiary text-right">母</span>
        <span class="text-[11px] text-text-tertiary text-right">合计</span>
        <template v-for="b in barns" :key="b.name">
          <span class="text-text-secondary truncate" :title="b.name">{{ b.name }}</span>
          <span class="text-right" style="color: #3b82c4">{{ b.male ?? 0 }}</span>
          <span class="text-right" style="color: #c9568f">{{ b.female ?? 0 }}</span>
          <span class="text-right text-text-primary font-medium">{{ b.count ?? 0 }}</span>
        </template>
      </div>
      <div class="grid grid-cols-[1fr_34px_34px_40px] gap-x-1 items-center border-t border-border mt-1.5 pt-1.5">
        <span class="text-text-primary font-semibold">总计</span>
        <span class="text-right font-medium" style="color: #3b82c4">{{ totals.male }}</span>
        <span class="text-right font-medium" style="color: #c9568f">{{ totals.female }}</span>
        <span class="text-right text-text-primary font-semibold">{{ totals.all }}</span>
      </div>
    </div>

    <!-- F15 结果横幅 -->
    <div
      v-if="photo"
      class="absolute left-1/2 top-16 -translate-x-1/2 z-20 flex items-center gap-3 max-w-[600px] rounded-xl border border-[#bfe3cd] bg-[#effaf3]/95 px-4 py-2.5 shadow-sm"
    >
      <span class="w-2.5 h-2.5 rounded-full bg-[#2e9e6b] shrink-0" />
      <div class="min-w-0">
        <p class="text-[13px] font-medium text-[#1e5c3c]">
          已按照片生成「{{ photo.config.barn_name }}」场景 · 识别置信度 {{ Math.round(photo.confidence * 100) }}%
          <span v-if="photoRestoredAt" class="ml-1.5 text-[12px] font-normal text-[#4a7a60]">
            （{{ photoRestoredAt.slice(0, 10) }} 的识别结果，已落库可跨刷新恢复）
          </span>
        </p>
        <p class="text-[12px] text-[#4a7a60] truncate">{{ photo.summary }}</p>
      </div>
      <button
        type="button"
        @click="photo = null; photoRestoredAt = null; sceneDismissed = true"
        class="shrink-0 flex items-center gap-1 px-3 py-1 rounded-lg text-[12px] border border-[#9fd4b4] text-[#1e5c3c] hover:bg-white transition-colors"
      >
        <RotateCcw :size="12" :stroke-width="1.5" />
        恢复默认
      </button>
    </div>

    <!-- 告警定位横幅（F06 联动，与 F15 横幅错位不遮挡） -->
    <div
      v-if="penName"
      class="absolute left-1/2 top-28 -translate-x-1/2 z-20 flex items-center gap-3 max-w-[600px] rounded-xl border border-[#f0c5c1] bg-[#fff1f0]/95 px-4 py-2.5 shadow-sm"
    >
      <span class="w-2.5 h-2.5 rounded-full bg-[#e03e36] animate-breathe shrink-0" />
      <div class="min-w-0">
        <p class="text-[13px] font-medium text-[#8c2a24]">
          {{ penName }} · {{ ALERT_TYPE_LABEL[alertType ?? ''] ?? '告警' }} · 场景定位中
        </p>
        <p v-if="content" class="text-[12px] text-[#a85a52] truncate">{{ content }}</p>
      </div>
      <button
        type="button"
        @click="router.push({ path: '/showcase3d' })"
        class="shrink-0 px-3 py-1 rounded-lg text-[12px] border border-[#e6b5b1] text-[#8c2a24] hover:bg-white transition-colors"
      >
        退出定位
      </button>
    </div>

    <!-- F15 上传识别对话框 -->
    <div
      v-if="dlgOpen"
      class="absolute inset-0 z-30 bg-black/40 flex items-center justify-center"
      @click="dlgOpen = false"
    >
      <div class="bg-white rounded-2xl p-5 w-[420px] max-w-[92%] shadow-xl" @click.stop>
        <div class="flex items-center justify-between mb-1">
          <h3 class="text-[15px] font-semibold text-text-primary">拍照建场景</h3>
          <button type="button" @click="dlgOpen = false" class="text-text-tertiary hover:text-text-primary">
            <X :size="16" :stroke-width="1.5" />
          </button>
        </div>
        <p class="text-[12px] text-text-tertiary mb-3">
          上传自家场舍照片（数量不限，角度越多识别越准），AI 识别后生成专属 3D 场景（当前为演示识别，切真后接入多模态大模型）。
        </p>
        <input
          ref="fileInput"
          type="file"
          accept="image/*"
          multiple
          class="hidden"
          @change="onFiles"
        />
        <button
          type="button"
          @click="fileInput?.click()"
          class="w-full border border-dashed border-border rounded-xl py-8 text-[13px] text-text-secondary hover:bg-bg-subtle transition-colors"
        >
          点击选择照片（数量不限）
        </button>
        <div v-if="previews.length > 0" class="flex gap-2 mt-3">
          <img
            v-for="(p, i) in previews"
            :key="i"
            :src="p"
            :alt="`预览${i + 1}`"
            class="w-20 h-20 object-cover rounded-lg border border-border"
          />
        </div>
        <p v-if="err" class="text-[12px] text-danger mt-2">{{ err }}</p>
        <div class="flex justify-end gap-2 mt-4">
          <button
            type="button"
            @click="dlgOpen = false"
            class="px-4 py-1.5 rounded-lg text-[13px] border border-border text-text-secondary hover:bg-bg-subtle transition-colors"
          >
            取消
          </button>
          <button
            type="button"
            :disabled="busy || previews.length === 0"
            @click="doAnalyze"
            class="px-4 py-1.5 rounded-lg text-[13px] text-white bg-primary hover:bg-primary-600 disabled:opacity-50 transition-colors"
          >
            {{ busy ? '识别中…' : '开始识别' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
