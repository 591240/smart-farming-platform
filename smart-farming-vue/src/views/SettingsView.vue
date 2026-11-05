<script setup lang="ts">
// 系统设置 — 通知偏好 + F08 气象数据源 + 演示环境说明
//
// 2026-10-23 移除「使用模拟设备数据」开关：浏览器造数引擎整体下线（见 lib/sensor-engine 的
// 删除记录），环境数据只剩真实来源——MQTT 桥接（backend/app/services/mqtt_bridge.py）
// 或设备带 X-Device-Key 直连 /device/ingest。设置页不再提供"让数据看起来在动"的开关。
// 其余两处收口沿用 2026-10-16 口径：
// ② 天气 provider/key 配置卡：AMAP Key 已移到后端根 `.env`（01-架构 G-03），前端填的 key
//    不再参与任何真后端请求，故改为"来源检测"卡，不再假装是配置项；
// ③ 「载入 HITL 演示账号」「载入故事数据」在真后端下只会回显一段说明（写不了真库），
//    现在去掉假按钮，直接指往各自真入口（用户管理页 / backend 脚本）。

import { ref } from 'vue';
import { Bell, Info, ClipboardCheck, CloudSun, ChevronDown } from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { apiGetWeather } from '@/api/weather-api';
import { isAlertPushEnabled, setAlertPushEnabled } from '@/lib/alert-stream';

const alertPush = ref(isAlertPushEnabled());
const demoOpen = ref(false);

function toggleAlertPush() {
  const next = !alertPush.value;
  alertPush.value = next;
  setAlertPushEnabled(next);
}

// ===== F08 气象数据源（只读来源 + 连通自检；取数与密钥都在服务端）=====
const msg = ref<{ ok: boolean; text: string } | null>(null);
const testing = ref(false);

function probe() {
  testing.value = true;
  msg.value = null;
  apiGetWeather()
    .then((r) => {
      msg.value = r.available
        ? {
            ok: true,
            text: `连通正常 —— 服务端取数 source=${r.source ?? 'live'}${
              r.degraded ? '（降级读缓存：上游失败或超时）' : ''
            }`,
          }
        : {
            ok: false,
            text: `无数据 —— ${
              r.reason === 'no_farm' ? '尚未创建养殖场' : '养殖场地址不完整（缺 adcode），服务端无法定位到县级区域'
            }`,
          };
    })
    .catch((e) => (msg.value = { ok: false, text: `连通失败 —— ${e instanceof Error ? e.message : String(e)}` }))
    .finally(() => (testing.value = false));
}

// 手写 Switch 的文案类（原 shadcn Switch 的 data-state 样式在此按状态条件套用）
const switchRootCls =
  'peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50';
const switchThumbCls =
  'pointer-events-none block h-4 w-4 rounded-full bg-background shadow-lg ring-0 transition-transform';

const demoNoteCls = 'px-3.5 py-2.5 rounded-[10px] text-[12.5px] leading-[1.8]';
const demoNoteStyle = {
  backgroundColor: 'rgba(46,158,107,.06)',
  border: '1px solid rgba(46,158,107,.18)',
  color: 'var(--text-secondary)',
};
const codeCls = 'px-1 rounded bg-bg-subtle';
</script>

<template>
  <div class="px-8 py-6 max-w-[760px]">
    <div class="mb-6">
      <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4]">系统设置</h1>
      <p class="text-[14px] text-text-secondary mt-0.5">通知与演示环境偏好</p>
    </div>

    <div class="bg-card rounded-[12px] e1 px-6 py-2 mb-4">
      <div class="flex items-center justify-between py-4 border-b border-border last:border-b-0">
        <div class="flex items-start gap-3">
          <span class="w-9 h-9 rounded-[10px] bg-bg-subtle flex items-center justify-center text-text-secondary shrink-0 mt-0.5">
            <Bell :size="16" :stroke-width="1.5" />
          </span>
          <div>
            <div class="text-[14px] font-medium text-text-primary">告警实时推送</div>
            <div class="text-[12px] text-text-tertiary mt-0.5">
              传感器超标、气象预警经 WebSocket 推至当前页面；关闭后仅靠各页轮询兜底（下次进入相关页面生效）
            </div>
          </div>
        </div>
        <button
          type="button"
          role="switch"
          :aria-checked="alertPush"
          :data-state="alertPush ? 'checked' : 'unchecked'"
          @click="toggleAlertPush"
          :class="[switchRootCls, alertPush ? 'bg-primary' : 'bg-input']"
        >
          <span
            :class="[switchThumbCls, alertPush ? 'translate-x-4' : 'translate-x-0']"
            :data-state="alertPush ? 'checked' : 'unchecked'"
          />
        </button>
      </div>
      <div class="flex items-center justify-between py-4 border-b border-border last:border-b-0">
        <div class="flex items-start gap-3">
          <span class="w-9 h-9 rounded-[10px] bg-bg-subtle flex items-center justify-center text-text-secondary shrink-0 mt-0.5">
            <CloudSun :size="16" :stroke-width="1.5" />
          </span>
          <div>
            <div class="text-[14px] font-medium text-text-primary">环境数据来源</div>
            <div class="text-[12px] text-text-tertiary mt-0.5">
              读数只来自真实设备：EMQX 经 MQTT 桥接入库，或设备带 X-Device-Key 直连 /device/ingest。没有设备时曲线为空态，不再由浏览器造数
            </div>
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked="false"
          data-state="unchecked"
          disabled
          :class="[switchRootCls, 'bg-input']"
        >
          <span :class="[switchThumbCls, 'translate-x-0']" data-state="unchecked" />
        </button>
      </div>
    </div>

    <div class="bg-card rounded-[12px] e1 px-6 py-5 mb-4">
      <div class="flex items-start gap-3 mb-4">
        <span class="w-9 h-9 rounded-[10px] bg-bg-subtle flex items-center justify-center text-text-secondary shrink-0">
          <CloudSun :size="16" :stroke-width="1.5" />
        </span>
        <div>
          <div class="text-[14px] font-medium text-text-primary">气象数据源</div>
          <div class="text-[12px] text-text-tertiary mt-0.5">
            取数在服务端：高德 Key 配在根目录 <code :class="codeCls">.env</code> 的
            <code :class="codeCls">AMAP_WEATHER_KEY</code>（01-架构 G-03，已不再打进前端产物）。
            这里只检测连通性并如实标出数据来源。
          </div>
        </div>
      </div>

      <div class="flex items-center gap-3">
        <button
          @click="probe"
          :disabled="testing"
          class="px-4 py-2 rounded-[10px] text-[13.5px] font-medium bg-primary-50 text-primary-700 hover:bg-primary-100 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {{ testing ? '检测中…' : '检测气象连通性' }}
        </button>
        <span
          v-if="msg"
          :class="'text-[12.5px] ' + (msg.ok ? 'text-primary-700' : 'text-[#c0392b]')"
          role="status"
        >
          {{ msg.text }}
        </span>
      </div>
    </div>

    <!-- 演示环境：只说真入口，不提供"写了不算数"的按钮 -->
    <div class="bg-card rounded-[12px] e1 px-6 py-4 mb-4">
      <button @click="demoOpen = !demoOpen" class="w-full flex items-center justify-between gap-3">
        <div class="flex items-start gap-3 text-left">
          <span class="w-9 h-9 rounded-[10px] bg-bg-subtle flex items-center justify-center text-text-secondary shrink-0">
            <ClipboardCheck :size="16" :stroke-width="1.5" />
          </span>
          <div>
            <div class="text-[14px] font-medium text-text-primary">演示环境（演示走查用）</div>
            <div class="text-[12px] text-text-tertiary mt-0.5">
              HITL 演示账号与病例 · 3 天养殖故事演示租户（走查步骤见 README「验证」一节）
            </div>
          </div>
        </div>
        <ChevronDown
          :size="16"
          :stroke-width="1.5"
          :class="cn('text-text-tertiary shrink-0 transition-transform', demoOpen && 'rotate-180')"
        />
      </button>
      <div v-if="demoOpen" class="mt-4 pt-4 border-t border-border space-y-3">
        <div :class="demoNoteCls" :style="demoNoteStyle">
          <div class="text-[13px] font-medium text-text-primary mb-1">兽医 / 管理员账号</div>
          由管理员在「用户管理」创建（<code>POST /admin/user</code>；兽医不开放注册）。首个管理员账号需在后端根
          <code>.env</code> 配 <code>ADMIN_SEED_PHONE</code> /
          <code>ADMIN_SEED_PASSWORD</code> 才会初始化——
          刻意不给默认凭据（01-架构 G-06）。高危待复核病例可在 AI 问诊就高危主诉自然产生（
          <code>hitl_gate</code> 节点）。
        </div>
        <div :class="demoNoteCls" :style="demoNoteStyle">
          <div class="text-[13px] font-medium text-text-primary mb-1">3 天养殖故事演示数据</div>
          在 <code>backend/</code> 内执行 <code>python seed_story.py</code>：走的是同一批 HTTP 接口
          （Day1 告警→问诊→用药、Day2 配种→分娩、Day3 休药期拦截与强制出栏留痕），写真库、可幂等重复执行。
          演示租户为 13000000010。
        </div>
        <div :class="demoNoteCls" :style="demoNoteStyle">
          <div class="text-[13px] font-medium text-text-primary mb-1">重置与回归</div>
          清空业务库用 <code>python scripts/reset_db.py</code>；跑后端回归请指向测试库
          <code>DATABASE_URL=…/smart_farm_test AI_PORT=8001 python run.py</code> +
          <code>python smoke_*.py</code>，不要把冒烟数据写进演示库。
        </div>
      </div>
    </div>

    <div class="bg-card rounded-[12px] e1 px-6 py-5">
      <div class="flex items-start gap-3">
        <span class="w-9 h-9 rounded-[10px] bg-bg-subtle flex items-center justify-center text-text-secondary shrink-0">
          <Info :size="16" :stroke-width="1.5" />
        </span>
        <div>
          <div class="text-[14px] font-medium text-text-primary">云上牧场 · 智慧养殖多智能体平台</div>
          <div class="text-[12px] text-text-tertiary mt-0.5">
            前端 V1.0 · 设计规范 06 V1.4 · 业务数据全部来自真后端；浏览器 mock 内核已于 2026-10-16 下线，
            存档在 <code>_archive/mock-retired-20261016/</code>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
