<script setup lang="ts">
// 系统设置 — 通知偏好 + F08 气象数据源
//
// 2026-10-23 移除「使用模拟设备数据」开关：浏览器造数引擎整体下线（见 lib/sensor-engine 的
// 删除记录），环境数据只剩真实来源——MQTT 桥接（backend/app/services/mqtt_bridge.py）
// 或设备带 X-Device-Key 直连 /device/ingest。设置页不再提供"让数据看起来在动"的开关。
// 2026-11-05 移除「演示环境」说明卡与 mock 历史文案（浏览器 mock 内核 2026-10-16 下线）。

import { ref } from 'vue';
import { Bell, Info, CloudSun } from 'lucide-vue-next';
import { apiGetWeather } from '@/api/weather-api';
import { isAlertPushEnabled, setAlertPushEnabled } from '@/lib/alert-stream';

const alertPush = ref(isAlertPushEnabled());

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

const codeCls = 'px-1 rounded bg-bg-subtle';
</script>

<template>
  <div class="px-8 py-6 max-w-[760px]">
    <div class="mb-6">
      <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4]">系统设置</h1>
      <p class="text-[14px] text-text-secondary mt-0.5">通知与数据源偏好</p>
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

    <!-- 版本信息 -->
    <div class="bg-card rounded-[12px] e1 px-6 py-5">
      <div class="flex items-start gap-3">
        <span class="w-9 h-9 rounded-[10px] bg-bg-subtle flex items-center justify-center text-text-secondary shrink-0">
          <Info :size="16" :stroke-width="1.5" />
        </span>
        <div>
          <div class="text-[14px] font-medium text-text-primary">云上牧场 · 智慧养殖多智能体平台</div>
          <div class="text-[12px] text-text-tertiary mt-0.5">
            前端 V1.0 · 设计规范 06 V1.4
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
