<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { Check, ChevronLeft, Leaf } from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/stores/auth';
import {
  apiCreateFarm,
  apiCreatePen,
  apiUpdateMainSpecies,
} from '@/api/auth-api';
import { ApiError } from '@/lib/api-contract';
import { PROVINCES, resolveRegion } from '@/lib/region-data';
import { SPECIES_LIST, stageOptions } from '@/lib/species-data';
import FieldLabel from '@/components/onboarding/FieldLabel.vue';
import ErrorLine from '@/components/onboarding/ErrorLine.vue';
import RegionSelect from '@/components/onboarding/RegionSelect.vue';
import VarietySelect from '@/components/onboarding/VarietySelect.vue';
import Stepper from '@/components/onboarding/Stepper.vue';

type Step = 1 | 2 | 3;

const router = useRouter();
const auth = useAuthStore();
const step = ref<Step>(1);

// Step1
const species = ref<string>(auth.user?.main_species_code ?? '');
watch(
  () => auth.user?.main_species_code,
  (v) => {
    if (v) species.value = v;
  },
);
const s1Loading = ref(false);
const s1Err = ref<string | null>(null);

// Step2
const farmName = ref('');
const provCode = ref('');
const cityCode = ref('');
const countyCode = ref('');
const address = ref('');
const contact = ref('');
const farmId = ref<number | null>(null);
const s2Loading = ref(false);
const s2Err = ref<string | null>(null);

// Step3
const penName = ref('');
const varietyCode = ref<string>('common');
const maleCount = ref(0);
const femaleCount = ref(0);
const batchStage = ref<string>('');
const s3Loading = ref(false);
const s3Err = ref<string | null>(null);

const province = computed(() => PROVINCES.find((p) => p.code === provCode.value));
const city = computed(() => province.value?.children?.find((c) => c.code === cityCode.value));
const county = computed(() => city.value?.children?.find((d) => d.code === countyCode.value));
const region = computed(() => resolveRegion(provCode.value, cityCode.value, countyCode.value));

const steps: { n: Step; label: string }[] = [
  { n: 1, label: '选主物种' },
  { n: 2, label: '建养殖场' },
  { n: 3, label: '建圈舍' },
];

function inputCls(hasError: boolean): string {
  return cn(
    'w-full h-10 px-3 rounded-[10px] bg-card border text-[14px] text-text-primary outline-none transition-colors',
    'focus:ring-2 focus:ring-primary/20',
    hasError ? 'border-danger' : 'border-border-strong focus:border-primary',
  );
}

function errText(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return '网络异常，请重试';
}

async function submitStep1() {
  if (!species.value) return;
  s1Loading.value = true;
  s1Err.value = null;
  try {
    const u = await apiUpdateMainSpecies(species.value);
    auth.setUser(u);
    step.value = 2;
  } catch (err) {
    s1Err.value = errText(err);
  } finally {
    s1Loading.value = false;
  }
}

async function submitStep2() {
  s2Loading.value = true;
  s2Err.value = null;
  try {
    if (!farmName.value.trim()) throw new ApiError(42201, '请填写养殖场名称');
    if (!region.value) throw new ApiError(42201, '请选择省市区');
    const res = await apiCreateFarm({
      farm_name: farmName.value,
      province: region.value.province,
      city: region.value.city,
      county: region.value.county,
      adcode: region.value.adcode,
      address: address.value || undefined,
      contact_person: contact.value || undefined,
    });
    farmId.value = res.farm_id;
    await auth.refreshUser();
    step.value = 3;
  } catch (err) {
    s2Err.value = errText(err);
  } finally {
    s2Loading.value = false;
  }
}

async function submitStep3() {
  s3Loading.value = true;
  s3Err.value = null;
  try {
    if (!farmId.value) throw new ApiError(42201, '请先完成第 2 步');
    await apiCreatePen({
      farm_id: farmId.value,
      pen_name: penName.value,
      variety_code: varietyCode.value,
      male_count: maleCount.value,
      female_count: femaleCount.value,
      batch_stage: batchStage.value || null,
    });
    router.replace('/dashboard');
  } catch (err) {
    s3Err.value = errText(err);
  } finally {
    s3Loading.value = false;
  }
}

function skipStep3() {
  router.replace('/dashboard');
}

function setProv(v: string) {
  provCode.value = v;
  cityCode.value = '';
  countyCode.value = '';
}
function setCity(v: string) {
  cityCode.value = v;
  countyCode.value = '';
}
</script>

<template>
  <div class="min-h-screen bg-bg-page flex flex-col items-center px-6 py-10">
    <!-- Logo -->
    <div class="flex items-center gap-2.5 mb-10">
      <div class="w-9 h-9 rounded-[10px] bg-primary flex items-center justify-center">
        <Leaf :size="18" :stroke-width="1.5" class="text-white" />
      </div>
      <span class="text-[17px] font-semibold text-text-primary">云上牧场</span>
    </div>

    <!-- 步骤条（可回退） -->
    <div class="flex items-center gap-0 mb-10 select-none">
      <template v-for="(s, i) in steps" :key="s.n">
        <div
          v-if="i > 0"
          class="w-14 sm:w-20 h-[2px] rounded-full mx-2 transition-colors duration-[var(--duration-base)]"
          :style="{ backgroundColor: step > s.n ? 'var(--primary-500)' : 'var(--border)' }"
        />
        <button
          type="button"
          :disabled="s.n >= step"
          :class="
            cn(
              'flex items-center gap-2 rounded-full pl-1.5 pr-4 py-1.5 transition-colors duration-[var(--duration-fast)]',
              s.n < step && 'hover:bg-primary-50 cursor-pointer',
              s.n >= step && 'cursor-default',
            )
          "
          @click="step = s.n"
        >
          <span
            class="w-7 h-7 rounded-full flex items-center justify-center text-[13px] font-medium transition-colors"
            :style="
              step > s.n
                ? { backgroundColor: 'var(--primary-500)', color: '#fff' }
                : step === s.n
                  ? { backgroundColor: 'var(--primary-100)', color: 'var(--primary-700)', boxShadow: '0 0 0 2px var(--primary-200)' }
                  : { backgroundColor: 'var(--bg-inset)', color: 'var(--text-tertiary)' }
            "
          >
            <Check v-if="step > s.n" :size="14" :stroke-width="2.5" />
            <template v-else>{{ s.n }}</template>
          </span>
          <span :class="cn('text-[13.5px]', step >= s.n ? 'text-text-primary font-medium' : 'text-text-tertiary')">
            {{ s.label }}
          </span>
        </button>
      </template>
    </div>

    <!-- ===== 第 1 步：选主物种（FR-04） ===== -->
    <div v-if="step === 1" class="w-full max-w-[640px] animate-slide-in">
      <div class="bg-card rounded-[16px] e1 p-8">
        <h2 class="text-[19px] font-semibold text-text-primary">你主要养什么？</h2>
        <p class="text-[13.5px] text-text-secondary mt-1 mb-6">
          主物种决定知识库、品种与告警阈值的配置路由，之后可在个人中心修改
        </p>

        <div class="grid grid-cols-3 gap-3">
          <button
            v-for="s in SPECIES_LIST"
            :key="s.species_code"
            type="button"
            :aria-pressed="species === s.species_code"
            :class="
              cn(
                'relative flex flex-col items-center gap-1.5 py-6 rounded-[14px] border-[1.5px] transition-all duration-[var(--duration-fast)] hover-lift',
                species === s.species_code
                  ? 'border-primary bg-primary-50'
                  : 'border-border bg-card hover:border-border-strong',
              )
            "
            @click="species = s.species_code"
          >
            <span
              v-if="species === s.species_code"
              class="absolute top-2.5 right-2.5 w-5 h-5 rounded-full bg-primary flex items-center justify-center animate-slide-in"
            >
              <Check :size="12" :stroke-width="2.5" class="text-white" />
            </span>
            <img
              :src="`/species/${s.species_code}.png`"
              :alt="s.species_name"
              class="w-12 h-12 rounded-full object-cover bg-primary-100"
            />
            <span class="text-[15px] font-medium text-text-primary mt-1">{{ s.species_name }}</span>
            <span class="text-[11.5px] text-text-tertiary text-center px-2 leading-[1.5]">{{ s.desc }}</span>
          </button>
        </div>

        <ErrorLine v-if="s1Err" :msg="s1Err" />

        <button
          type="button"
          :disabled="!species || s1Loading"
          :class="
            cn(
              'w-full h-11 mt-7 rounded-[10px] text-[14.5px] font-medium text-white transition-all flex items-center justify-center gap-2',
              (!species || s1Loading) && 'opacity-50 cursor-not-allowed',
            )
          "
          style="background: linear-gradient(180deg, var(--primary-500), var(--primary-600))"
          @click="submitStep1"
        >
          <span v-if="s1Loading" class="w-4 h-4 rounded-full border-2 border-white/40 border-t-white animate-spin" />
          {{ s1Loading ? '保存中…' : '下一步' }}
        </button>
        <p v-if="!species" class="mt-2 text-[12px] text-text-tertiary text-center">请先选择主物种</p>
      </div>
    </div>

    <!-- ===== 第 2 步：创建养殖场（FR-05） ===== -->
    <div v-if="step === 2" class="w-full max-w-[560px] animate-slide-in">
      <div class="bg-card rounded-[16px] e1 p-8">
        <button
          type="button"
          class="flex items-center gap-1 text-[12.5px] text-text-tertiary hover:text-primary transition-colors mb-4"
          @click="step = 1"
        >
          <ChevronLeft :size="14" /> 返回上一步
        </button>
        <h2 class="text-[19px] font-semibold text-text-primary">填写养殖场信息</h2>
        <div class="mb-6" />

        <div class="mb-4">
          <FieldLabel for="ob-farm-name" text="养殖场名称" required />
          <input
            id="ob-farm-name"
            v-model="farmName"
            maxlength="30"
            placeholder="请输入养殖场名称"
            :class="inputCls(!!s2Err && !farmName.trim())"
          />
        </div>

        <div class="mb-4">
          <FieldLabel for="ob-province" text="省 / 市 / 区县" required />
          <div class="grid grid-cols-3 gap-2">
            <RegionSelect
              id="ob-province"
              v-model:value="provCode"
              placeholder="选择省"
              :options="PROVINCES"
              @update:value="setProv"
            />
            <RegionSelect
              id="ob-city"
              v-model:value="cityCode"
              placeholder="选择市"
              :options="province?.children ?? []"
              :disabled="!provCode"
              @update:value="setCity"
            />
            <RegionSelect
              id="ob-county"
              v-model:value="countyCode"
              placeholder="选择区县"
              :options="city?.children ?? []"
              :disabled="!cityCode"
            />
          </div>
          <p v-if="region" class="mt-1.5 text-[12px] text-text-tertiary flex items-center gap-1.5">
            行政区划编码
            <code class="tnum px-1.5 py-0.5 rounded-[6px] bg-bg-subtle text-text-secondary text-[11.5px]">
              {{ region.adcode }}
            </code>
            （自动带出，只读）
          </p>
        </div>

        <div class="mb-4">
          <FieldLabel for="ob-address" text="详细地址" optional />
          <input
            id="ob-address"
            v-model="address"
            maxlength="60"
            placeholder="如：XX 镇 XX 村（选填）"
            :class="inputCls(false)"
          />
        </div>

        <div class="grid grid-cols-2 gap-3 mb-4">
          <div>
            <FieldLabel for="ob-contact" text="联系人" optional />
            <input
              id="ob-contact"
              v-model="contact"
              maxlength="20"
              :placeholder="auth.user?.nickname ?? ''"
              :class="inputCls(false)"
            />
          </div>
          <div>
            <FieldLabel text="联系电话" optional />
            <input
              :value="auth.user?.phone ?? ''"
              readonly
              class="w-full h-10 px-3 rounded-[10px] bg-bg-inset border border-border text-[13.5px] text-text-tertiary tnum cursor-not-allowed"
            />
          </div>
        </div>

        <ErrorLine v-if="s2Err" :msg="s2Err" />

        <button
          type="button"
          :disabled="s2Loading"
          :class="
            cn(
              'w-full h-11 mt-5 rounded-[10px] text-[14.5px] font-medium text-white transition-all flex items-center justify-center gap-2',
              s2Loading && 'opacity-70 cursor-not-allowed',
            )
          "
          style="background: linear-gradient(180deg, var(--primary-500), var(--primary-600))"
          @click="submitStep2"
        >
          <span v-if="s2Loading" class="w-4 h-4 rounded-full border-2 border-white/40 border-t-white animate-spin" />
          {{ s2Loading ? '创建中…' : '创建养殖场，下一步' }}
        </button>
        <p class="mt-2.5 text-[12px] text-text-tertiary text-center">
          完成本步即完成必填引导，下一步可随时跳过
        </p>
      </div>
    </div>

    <!-- ===== 第 3 步：创建圈舍（FR-06，可跳过） ===== -->
    <div v-if="step === 3" class="w-full max-w-[560px] animate-slide-in">
      <div class="bg-card rounded-[16px] e1 p-8">
        <button
          type="button"
          class="flex items-center gap-1 text-[12.5px] text-text-tertiary hover:text-primary transition-colors mb-4"
          @click="step = 2"
        >
          <ChevronLeft :size="14" /> 返回上一步
        </button>
        <h2 class="text-[19px] font-semibold text-text-primary">创建第一个圈舍</h2>
        <p class="text-[13.5px] text-text-secondary mt-1 mb-6">
          圈舍物种已继承主物种（{{ SPECIES_LIST.find((s) => s.species_code === species)?.species_name ?? '猪' }}），不可修改
        </p>

        <div class="mb-4">
          <FieldLabel for="ob-pen-name" text="圈舍名称" />
          <input
            id="ob-pen-name"
            v-model="penName"
            maxlength="20"
            placeholder="如：1 号育肥圈（留空自动命名）"
            :class="inputCls(false)"
          />
        </div>

        <div class="mb-4">
          <FieldLabel for="ob-variety" text="品种" required />
          <VarietySelect id="ob-variety" v-model:value="varietyCode" :species-code="species" />
        </div>

        <div class="grid grid-cols-2 gap-3 mb-4">
          <Stepper
            id="ob-male"
            label="公畜数量"
            :value="maleCount"
            @update:value="(v) => (maleCount = Math.max(0, Math.min(99999, v)))"
          />
          <Stepper
            id="ob-female"
            label="母畜数量"
            :value="femaleCount"
            @update:value="(v) => (femaleCount = Math.max(0, Math.min(99999, v)))"
          />
        </div>

        <div class="mb-2">
          <FieldLabel for="ob-stage" text="生长阶段" optional />
          <select
            id="ob-stage"
            v-model="batchStage"
            class="w-full h-10 px-3 rounded-[10px] bg-card border border-border-strong text-[14px] text-text-primary outline-none focus:border-primary transition-colors"
          >
            <option value="">暂不确定</option>
            <option v-for="o in stageOptions(species || 'pig')" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
        </div>

        <ErrorLine v-if="s3Err" :msg="s3Err" />

        <button
          type="button"
          :disabled="s3Loading"
          :class="
            cn(
              'w-full h-11 mt-5 rounded-[10px] text-[14.5px] font-medium text-white transition-all flex items-center justify-center gap-2',
              s3Loading && 'opacity-70 cursor-not-allowed',
            )
          "
          style="background: linear-gradient(180deg, var(--primary-500), var(--primary-600))"
          @click="submitStep3"
        >
          <span v-if="s3Loading" class="w-4 h-4 rounded-full border-2 border-white/40 border-t-white animate-spin" />
          {{ s3Loading ? '创建中…' : '创建圈舍并进入看板' }}
        </button>
        <button
          type="button"
          class="w-full h-10 mt-2.5 rounded-[10px] text-[13.5px] text-text-secondary hover:text-primary hover:bg-primary-50 transition-colors"
          @click="skipStep3"
        >
          跳过，稍后添加
        </button>
      </div>
    </div>

    <p class="mt-8 text-[11.5px] text-text-tertiary">
      养殖场与圈舍数据已保存至后端数据库 · 引导完成率与时长为本阶段核心验收指标
    </p>
  </div>
</template>
