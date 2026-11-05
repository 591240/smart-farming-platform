<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { Eye, EyeOff, Leaf, ShieldCheck, LineChart, HeartPulse } from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/stores/auth';
import { ApiError, isValidPhone, validatePassword } from '@/lib/api-contract';

const router = useRouter();
const auth = useAuthStore();
const mode = ref<'login' | 'register'>('login');

const phone = ref('');
const nickname = ref('');
const password = ref('');
const password2 = ref('');
const showPwd = ref(false);
const fieldErr = ref<Record<string, string>>({});
const topErr = ref<string | null>(null);
const loading = ref(false);

const strength = computed(() => {
  if (!password.value) return { level: 0, label: '' };
  let level = 0;
  if (password.value.length >= 8) level++;
  if (/[A-Za-z]/.test(password.value) && /\d/.test(password.value)) level++;
  if (password.value.length >= 12 && /[^A-Za-z0-9]/.test(password.value)) level++;
  return { level, label: ['弱', '弱', '中', '强'][level] };
});

function switchMode(m: 'login' | 'register') {
  mode.value = m;
  fieldErr.value = {};
  topErr.value = null;
}

async function handleSubmit() {
  fieldErr.value = {};
  topErr.value = null;

  const errs: Record<string, string> = {};
  if (!isValidPhone(phone.value)) errs.phone = '请输入 11 位大陆手机号';
  if (mode.value === 'register') {
    const pwdErr = validatePassword(password.value);
    if (pwdErr) errs.password = pwdErr;
    if (password2.value !== password.value) errs.password2 = '两次输入的密码不一致';
  }
  if (Object.keys(errs).length > 0) {
    fieldErr.value = errs;
    return;
  }

  loading.value = true;
  try {
    const user =
      mode.value === 'login'
        ? await auth.login(phone.value, password.value)
        : await auth.register(phone.value, password.value, nickname.value || undefined);
    router.replace(user.guide_done ? '/dashboard' : '/onboarding');
  } catch (err) {
    if (err instanceof ApiError) {
      const data = err.data as { field?: string } | undefined;
      if (err.code === 40901) {
        // EX-A1：已注册 → 引导去登录
        fieldErr.value = { phone: err.message };
        mode.value = 'login';
      } else if (err.code === 42201 && data?.field) {
        fieldErr.value = { [data.field]: err.message };
      } else {
        topErr.value = err.message;
      }
    } else {
      topErr.value = '网络异常，请重试';
    }
  } finally {
    loading.value = false;
  }
}

const inlineToast = ref<string | null>(null);
let toastTimer: number | null = null;
function toastInline(msg: string) {
  inlineToast.value = msg;
  if (toastTimer) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => (inlineToast.value = null), 2400);
}

const features = [
  { icon: HeartPulse, title: '异常早知道', desc: '传感器 + 视觉 + 气象，三路风险统一告警' },
  { icon: ShieldCheck, title: '用药有底线', desc: '违禁兽药拦截，休药期未满禁止出栏' },
  { icon: LineChart, title: '成本看得清', desc: '配方 → 采食 → 日增重 → 料肉比全链路' },
];
</script>

<template>
  <div class="min-h-screen grid lg:grid-cols-[1.1fr_1fr] bg-bg-page">
    <!-- ===== 左：品牌区（桌面端） ===== -->
    <div
      class="hidden lg:flex flex-col justify-between p-14 relative overflow-hidden"
      style="background: linear-gradient(160deg, var(--primary-900) 0%, var(--primary-700) 58%, var(--primary-600) 100%)"
    >
      <div
        class="absolute inset-0 pointer-events-none"
        style="background: radial-gradient(720px 320px at 14% 8%, rgba(232,162,61,.10), transparent 70%)"
      />
      <div class="relative">
        <div class="brand-logo flex items-center gap-3">
          <div class="w-11 h-11 rounded-[12px] bg-white/12 flex items-center justify-center ring-1 ring-white/20">
            <Leaf :size="22" :stroke-width="1.5" class="brand-leaf text-primary-200" />
          </div>
          <div>
            <div class="text-white text-[19px] font-semibold tracking-wide">云上牧场</div>
            <div class="text-primary-200 text-[11px] tracking-[4px]">CLOUD RANCH · AI</div>
          </div>
        </div>

        <h1 class="text-blur-hover mt-20 text-white text-[34px] font-semibold leading-[1.35] max-w-[460px] tracking-tight">
          让每一位养殖户，<br />
          都有一位 <span class="brand-glow text-primary-200">AI 兽医</span> 与
          <span class="brand-glow text-primary-200"> AI 营养师</span>。
        </h1>
        <p class="brand-sub mt-5 text-primary-100/85 text-[14px] leading-[1.8] max-w-[420px]">
          圈舍环境告警 · 疫病辅助诊断 · 日粮配方大脑 · 养殖台账自动生成，<br />
          围绕畜禽全生命周期的多智能体助手。
        </p>

        <div class="mt-12 space-y-4 max-w-[380px]">
          <div v-for="f in features" :key="f.title" class="brand-item flex items-start gap-3.5">
            <div class="brand-item-icon w-9 h-9 rounded-[10px] bg-white/10 ring-1 ring-white/15 flex items-center justify-center shrink-0">
              <component :is="f.icon" :size="17" :stroke-width="1.5" class="text-primary-200" />
            </div>
            <div>
              <div class="text-white/95 text-[14px] font-medium">{{ f.title }}</div>
              <div class="text-primary-100/70 text-[12.5px] leading-[1.6]">{{ f.desc }}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="relative text-primary-100/50 text-[11.5px] leading-[1.7]">
        AI 建议仅作辅助参考，不能替代执业兽医诊断与处方。
      </div>
    </div>

    <!-- ===== 右：表单区 ===== -->
    <div class="flex flex-col items-center justify-center px-6 py-12">
      <div class="w-full max-w-[400px]">
        <div class="lg:hidden flex items-center gap-2.5 mb-8">
          <div class="w-9 h-9 rounded-[10px] bg-primary flex items-center justify-center">
            <Leaf :size="18" :stroke-width="1.5" class="text-white" />
          </div>
          <span class="text-[17px] font-semibold text-text-primary">云上牧场</span>
        </div>

        <h2 class="text-[22px] font-semibold text-text-primary tracking-tight">
          {{ mode === 'login' ? '欢迎回来' : '创建账号' }}
        </h2>
        <p class="text-[13.5px] text-text-secondary mt-1 mb-7">
          {{ mode === 'login' ? '登录后进入你的牧场看板' : '注册后 2 分钟完成引导，开始智慧养殖' }}
        </p>

        <div class="flex gap-1 bg-bg-subtle rounded-[10px] p-1 mb-6">
          <button
            v-for="m in (['login', 'register'] as const)"
            :key="m"
            type="button"
            :class="
              cn(
                'flex-1 py-2 rounded-[8px] text-[13.5px] transition-all duration-[var(--duration-fast)]',
                mode === m ? 'bg-card text-text-primary font-medium e1' : 'text-text-secondary hover:text-text-primary',
              )
            "
            @click="switchMode(m)"
          >
            {{ m === 'login' ? '登录' : '注册' }}
          </button>
        </div>

        <div
          v-if="topErr"
          class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px] animate-slide-in"
          style="background-color: rgba(217,83,79,.07); border: 1px solid rgba(217,83,79,.25); color: var(--danger)"
          role="alert"
        >
          {{ topErr }}
        </div>
        <div
          v-if="inlineToast"
          class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px] animate-slide-in"
          style="background-color: rgba(46,158,107,.08); border: 1px solid rgba(46,158,107,.25); color: var(--primary-700)"
        >
          {{ inlineToast }}
        </div>

        <form novalidate @submit.prevent="handleSubmit">
          <div class="mb-4">
            <label for="phone" class="block text-[13px] text-text-secondary mb-1.5">
              手机号 <span style="color: var(--danger)">*</span>
            </label>
            <input
              id="phone"
              type="tel"
              inputmode="numeric"
              maxlength="11"
              autocomplete="tel"
              :value="phone"
              placeholder="11 位手机号"
              :aria-invalid="!!fieldErr.phone"
              :class="
                cn(
                  'w-full h-11 px-3.5 rounded-[10px] bg-card border text-[14px] tnum text-text-primary outline-none transition-colors',
                  'focus:ring-2 focus:ring-primary/20',
                  fieldErr.phone ? 'border-danger' : 'border-border-strong focus:border-primary',
                )
              "
              @input="phone = ($event.target as HTMLInputElement).value.replace(/\D/g, '')"
            />
            <p v-if="fieldErr.phone" class="mt-1.5 text-[12px]" style="color: var(--danger)">{{ fieldErr.phone }}</p>
          </div>

          <div v-if="mode === 'register'" class="mb-4">
            <label for="nickname" class="block text-[13px] text-text-secondary mb-1.5">
              昵称 <span class="text-text-tertiary">（选填，默认取手机号）</span>
            </label>
            <input
              id="nickname"
              v-model="nickname"
              type="text"
              maxlength="20"
              autocomplete="nickname"
              placeholder="请输入养殖场名称"
              class="w-full h-11 px-3.5 rounded-[10px] bg-card border border-border-strong text-[14px] text-text-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-colors"
            />
          </div>

          <div class="mb-2">
            <label for="password" class="block text-[13px] text-text-secondary mb-1.5">
              密码 <span style="color: var(--danger)">*</span>
            </label>
            <div class="relative">
              <input
                id="password"
                v-model="password"
                :type="showPwd ? 'text' : 'password'"
                :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
                :placeholder="mode === 'register' ? '≥8 位，含字母和数字' : '请输入密码'"
                :aria-invalid="!!fieldErr.password"
                :class="
                  cn(
                    'w-full h-11 px-3.5 pr-10 rounded-[10px] bg-card border text-[14px] text-text-primary outline-none transition-colors',
                    'focus:ring-2 focus:ring-primary/20',
                    fieldErr.password ? 'border-danger' : 'border-border-strong focus:border-primary',
                  )
                "
              />
              <button
                type="button"
                class="absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary hover:text-text-secondary transition-colors"
                :aria-label="showPwd ? '隐藏密码' : '显示密码'"
                @click="showPwd = !showPwd"
              >
                <EyeOff v-if="showPwd" :size="16" :stroke-width="1.5" />
                <Eye v-else :size="16" :stroke-width="1.5" />
              </button>
            </div>
            <p v-if="fieldErr.password" class="mt-1.5 text-[12px]" style="color: var(--danger)">{{ fieldErr.password }}</p>
            <div v-if="mode === 'register' && password" class="mt-2 flex items-center gap-2">
              <div class="flex-1 flex gap-1">
                <div
                  v-for="i in [1, 2, 3]"
                  :key="i"
                  class="h-1 flex-1 rounded-full transition-colors duration-[var(--duration-base)]"
                  :style="{
                    backgroundColor:
                      strength.level >= i
                        ? ['var(--danger)', 'var(--warning)', 'var(--success)'][strength.level - 1]
                        : 'var(--bg-inset)',
                  }"
                />
              </div>
              <span class="text-[11px] text-text-tertiary">{{ strength.label }}</span>
            </div>
          </div>

          <div v-if="mode === 'register'" class="mb-4 mt-4">
            <label for="password2" class="block text-[13px] text-text-secondary mb-1.5">
              确认密码 <span style="color: var(--danger)">*</span>
            </label>
            <input
              id="password2"
              v-model="password2"
              :type="showPwd ? 'text' : 'password'"
              autocomplete="new-password"
              placeholder="再输入一次密码"
              :aria-invalid="!!fieldErr.password2"
              :class="
                cn(
                  'w-full h-11 px-3.5 rounded-[10px] bg-card border text-[14px] text-text-primary outline-none transition-colors',
                  'focus:ring-2 focus:ring-primary/20',
                  fieldErr.password2 ? 'border-danger' : 'border-border-strong focus:border-primary',
                )
              "
            />
            <p v-if="fieldErr.password2" class="mt-1.5 text-[12px]" style="color: var(--danger)">{{ fieldErr.password2 }}</p>
          </div>

          <button
            type="submit"
            :disabled="loading"
            :class="
              cn(
                'w-full h-11 mt-6 rounded-[10px] text-[14.5px] font-medium text-white transition-all',
                'flex items-center justify-center gap-2',
                loading ? 'opacity-70 cursor-not-allowed' : 'hover:opacity-95 active:scale-[.99]',
              )
            "
            style="background: linear-gradient(180deg, var(--primary-500), var(--primary-600)); box-shadow: 0 2px 8px rgba(46,158,107,.3), inset 0 1px 0 rgba(255,255,255,.18)"
          >
            <span v-if="loading" class="w-4 h-4 rounded-full border-2 border-white/40 border-t-white animate-spin" />
            {{ mode === 'login' ? '登录' : '注册并开始引导' }}
          </button>
        </form>
      </div>
    </div>
  </div>
</template>
