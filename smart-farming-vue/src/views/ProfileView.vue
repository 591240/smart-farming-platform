<script setup lang="ts">
// 个人中心（F01 FR-08 改主物种 / FR-09 改密码）
// 主物种：改动前 AlertDialog 确认（EX-08：历史数据零变动，仅影响新建圈舍默认物种）
// 改密码：旧密码校验（EX-A4）+ 强度规则 + 新旧不同校验；成功后当前会话保持有效（F01 决策 3）
// 注：改昵称接口不在 F01 范围（03-API 无对应契约），昵称暂为只读展示

import { computed, ref, watch } from 'vue';
import { Check, Eye, EyeOff } from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/stores/auth';
import { apiUpdateMainSpecies, apiUpdatePassword } from '@/api/auth-api';
import { ApiError, validatePassword } from '@/lib/api-contract';
import { SPECIES_LIST } from '@/lib/species-data';

const auth = useAuthStore();
const user = computed(() => auth.user);

// ===== FR-08 主物种 =====
const species = ref<string>(auth.user?.main_species_code ?? 'pig');
// 启动恢复（/user/me）完成后同步真实主物种，避免刷新后回显错误
watch(
  () => auth.user?.main_species_code,
  (code) => {
    if (code) species.value = code;
  },
);
const pendingSpecies = ref<string | null>(null);
const speciesLoading = ref(false);
const speciesErr = ref<string | null>(null);
const speciesSaved = ref(false);

const pendingSpeciesName = computed(
  () => SPECIES_LIST.find((s) => s.species_code === pendingSpecies.value)?.species_name,
);

function pickSpecies(id: string) {
  if (id === species.value) return;
  pendingSpecies.value = id; // 打开确认弹窗
}

async function confirmSpecies() {
  const target = pendingSpecies.value;
  if (!target) return;
  speciesLoading.value = true;
  speciesErr.value = null;
  try {
    const u = await apiUpdateMainSpecies(target);
    auth.setUser(u);
    species.value = target;
    pendingSpecies.value = null;
    speciesSaved.value = true;
    window.setTimeout(() => (speciesSaved.value = false), 2400);
  } catch (err) {
    speciesErr.value = err instanceof ApiError ? err.message : '网络异常，请重试';
  } finally {
    speciesLoading.value = false;
  }
}

// ===== FR-09 改密码 =====
const oldPwd = ref('');
const newPwd = ref('');
const newPwd2 = ref('');
const showPwd = ref(false);
const pwdErr = ref<Record<string, string>>({});
const pwdLoading = ref(false);
const pwdSaved = ref(false);

async function submitPassword() {
  const errs: Record<string, string> = {};
  if (!oldPwd.value) errs.old = '请输入当前密码';
  const strengthErr = validatePassword(newPwd.value);
  if (strengthErr) errs.new = strengthErr;
  if (oldPwd.value && newPwd.value && oldPwd.value === newPwd.value) errs.new = '新密码不能与当前密码相同';
  if (newPwd2.value !== newPwd.value) errs.confirm = '两次输入的新密码不一致';
  pwdErr.value = errs;
  if (Object.keys(errs).length > 0) return;

  pwdLoading.value = true;
  try {
    await apiUpdatePassword({ old_password: oldPwd.value, new_password: newPwd.value });
    oldPwd.value = '';
    newPwd.value = '';
    newPwd2.value = '';
    pwdSaved.value = true;
    window.setTimeout(() => (pwdSaved.value = false), 3000);
  } catch (err) {
    if (err instanceof ApiError) {
      if (err.code === 40103) errs.old = err.message; // EX-A4
      else if (err.code === 42201) errs.new = err.message;
      else errs.old = err.message;
      pwdErr.value = errs;
    } else {
      pwdErr.value = { old: '网络异常，请重试' };
    }
  } finally {
    pwdLoading.value = false;
  }
}

const inputBase =
  'w-full h-10 px-3 rounded-[10px] bg-bg-page border text-[14px] text-text-primary outline-none focus:ring-2 focus:ring-primary/20 transition-colors';
</script>

<template>
  <div class="px-8 py-6 max-w-[1400px]">
    <div class="mb-6">
      <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4]">个人中心</h1>
      <p class="text-[14px] text-text-secondary mt-0.5">维护账号信息与主物种偏好</p>
    </div>

    <!-- 12 栅格两栏：基础信息与主物种并排，修改密码整行但表单限宽保证可读行长 -->
    <div class="grid grid-cols-12 gap-4 items-start">

    <!-- ===== 基础信息 ===== -->
    <div class="col-span-12 lg:col-span-5 bg-card rounded-[12px] e1 p-6">
      <h2 class="text-[16px] font-semibold text-text-primary mb-5">基础信息</h2>

      <div class="mb-5">
        <label class="block text-[13px] text-text-secondary mb-1.5">昵称</label>
        <input
          :value="user?.nickname ?? '—'"
          readonly
          class="w-full h-10 px-3 rounded-[10px] bg-bg-inset border border-border text-[14px] text-text-tertiary cursor-not-allowed"
        />
        <p class="text-[12px] text-text-tertiary mt-1">修改昵称功能将在账户扩展版本开放</p>
      </div>

      <div>
        <label class="block text-[13px] text-text-secondary mb-1.5">手机号</label>
        <input
          :value="user?.phone ?? '—'"
          readonly
          class="w-full h-10 px-3 rounded-[10px] bg-bg-inset border border-border text-[14px] text-text-tertiary tnum cursor-not-allowed"
        />
        <p class="text-[12px] text-text-tertiary mt-1">手机号不支持自助修改</p>
      </div>
    </div>

    <!-- ===== FR-08 主物种 ===== -->
    <div class="col-span-12 lg:col-span-7 bg-card rounded-[12px] e1 p-6">
      <h2 class="text-[16px] font-semibold text-text-primary mb-5">主物种</h2>
      <div class="grid grid-cols-3 gap-3">
        <button
          v-for="s in SPECIES_LIST"
          :key="s.species_code"
          type="button"
          @click="pickSpecies(s.species_code)"
          :class="
            cn(
              'relative flex flex-col items-center gap-1.5 py-4 rounded-[12px] border-[1.5px] transition-all duration-[var(--duration-fast)] hover-lift',
              species === s.species_code
                ? 'border-primary bg-primary-50'
                : 'border-border bg-card hover:border-border-strong',
            )
          "
        >
          <span
            v-if="species === s.species_code"
            class="absolute top-2 right-2 w-5 h-5 rounded-full bg-primary flex items-center justify-center"
          >
            <Check :size="12" :stroke-width="2" class="text-white" />
          </span>
          <span class="w-10 h-10 rounded-full bg-primary-100 flex items-center justify-center text-[18px] font-semibold text-primary-700">
            {{ s.species_name }}
          </span>
          <span class="text-[14px] font-medium text-text-primary">{{ s.species_name }}</span>
          <span class="text-[12px] text-text-tertiary text-center px-1 leading-[1.5]">{{ s.desc }}</span>
        </button>
      </div>
      <p class="text-[12px] text-text-tertiary mt-2.5">
        更改主物种后历史数据保留，仅影响新建圈舍的默认品种
      </p>
      <p v-if="speciesErr" class="mt-1.5 text-[12.5px]" :style="{ color: 'var(--danger)' }">
        {{ speciesErr }}
      </p>
      <span v-if="speciesSaved" class="animate-slide-in mt-2 inline-flex items-center gap-1 text-[13px] text-success">
        <Check :size="14" :stroke-width="2" />
        主物种已更新，历史数据未变动
      </span>
    </div>

    <!-- ===== FR-09 修改密码 ===== -->
    <div class="col-span-12 bg-card rounded-[12px] e1 p-6">
      <h2 class="text-[16px] font-semibold text-text-primary mb-5">修改密码</h2>
      <form @submit.prevent="submitPassword" novalidate class="max-w-[560px]">
        <div class="mb-4">
          <label for="old-pwd" class="block text-[13px] text-text-secondary mb-1.5">
            当前密码
          </label>
          <input
            id="old-pwd"
            :type="showPwd ? 'text' : 'password'"
            autocomplete="current-password"
            v-model="oldPwd"
            :aria-invalid="!!pwdErr.old"
            :class="cn(inputBase, pwdErr.old ? 'border-danger' : 'border-border-strong')"
          />
          <p v-if="pwdErr.old" class="mt-1.5 text-[12px]" :style="{ color: 'var(--danger)' }">{{ pwdErr.old }}</p>
        </div>

        <div class="mb-4">
          <label for="new-pwd" class="block text-[13px] text-text-secondary mb-1.5">
            新密码 <span class="text-text-tertiary">（≥8 位，含字母和数字）</span>
          </label>
          <div class="relative">
            <input
              id="new-pwd"
              :type="showPwd ? 'text' : 'password'"
              autocomplete="new-password"
              v-model="newPwd"
              :aria-invalid="!!pwdErr.new"
              :class="cn(inputBase, 'pr-10', pwdErr.new ? 'border-danger' : 'border-border-strong')"
            />
            <button
              type="button"
              @click="showPwd = !showPwd"
              class="absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary hover:text-text-secondary transition-colors"
              :aria-label="showPwd ? '隐藏密码' : '显示密码'"
            >
              <EyeOff v-if="showPwd" :size="16" :stroke-width="1.5" />
              <Eye v-else :size="16" :stroke-width="1.5" />
            </button>
          </div>
          <p v-if="pwdErr.new" class="mt-1.5 text-[12px]" :style="{ color: 'var(--danger)' }">{{ pwdErr.new }}</p>
        </div>

        <div class="mb-1">
          <label for="new-pwd2" class="block text-[13px] text-text-secondary mb-1.5">
            确认新密码
          </label>
          <input
            id="new-pwd2"
            :type="showPwd ? 'text' : 'password'"
            autocomplete="new-password"
            v-model="newPwd2"
            :aria-invalid="!!pwdErr.confirm"
            :class="cn(inputBase, pwdErr.confirm ? 'border-danger' : 'border-border-strong')"
          />
          <p v-if="pwdErr.confirm" class="mt-1.5 text-[12px]" :style="{ color: 'var(--danger)' }">{{ pwdErr.confirm }}</p>
        </div>

        <div class="flex items-center gap-3 mt-5">
          <button
            type="submit"
            :disabled="pwdLoading"
            :class="
              cn(
                'px-5 py-2.5 bg-primary text-white rounded-[10px] text-[14px] font-medium transition-all',
                'flex items-center gap-2',
                pwdLoading ? 'opacity-70 cursor-not-allowed' : 'hover:bg-primary-600 active:scale-[.99]',
              )
            "
          >
            <span v-if="pwdLoading" class="w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin" />
            {{ pwdLoading ? '提交中…' : '更新密码' }}
          </button>
          <span v-if="pwdSaved" class="animate-slide-in text-[13px] text-success flex items-center gap-1">
            <Check :size="14" :stroke-width="2" />
            密码已更新，当前会话保持有效（下次登录请使用新密码）
          </span>
        </div>
      </form>
    </div>
    </div>

    <!-- FR-08 确认弹窗 -->
    <div v-if="pendingSpecies" class="fixed inset-0 z-50">
      <div class="fixed inset-0 z-50 bg-black/80" />
      <div
        class="fixed left-[50%] top-[50%] z-50 grid w-full max-w-[420px] translate-x-[-50%] translate-y-[-50%] gap-4 border bg-background p-6 shadow-lg duration-200 rounded-[16px] sm:rounded-lg"
        role="alertdialog"
        aria-modal="true"
      >
        <div class="flex flex-col space-y-2 text-center sm:text-left">
          <h2 class="text-[17px] font-semibold">确认更改主物种？</h2>
          <p class="text-[13.5px] leading-[1.7] text-muted-foreground">
            将主物种更改为<b class="text-text-primary">「{{ pendingSpeciesName }}」</b>。历史台账、告警、圈舍数据原样保留，仅影响之后新建圈舍的默认品种。
          </p>
        </div>
        <div class="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2">
          <button
            type="button"
            class="inline-flex items-center justify-center gap-2 whitespace-nowrap text-sm font-medium cursor-pointer transition-colors focus-visible:outline-none border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2 mt-2 sm:mt-0 rounded-[10px]"
            @click="pendingSpecies = null"
          >
            取消
          </button>
          <button
            type="button"
            :disabled="speciesLoading"
            class="inline-flex items-center justify-center gap-2 whitespace-nowrap text-sm font-medium cursor-pointer transition-colors focus-visible:outline-none h-9 px-4 py-2 bg-primary text-primary-foreground shadow hover:bg-primary-600 disabled:pointer-events-none disabled:opacity-50 disabled:cursor-not-allowed rounded-[10px]"
            @click="confirmSpecies"
          >
            {{ speciesLoading ? '保存中…' : '确认更改' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
