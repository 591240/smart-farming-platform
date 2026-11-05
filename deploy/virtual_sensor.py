#!/usr/bin/env python3
"""虚拟温度传感器 —— 定时向云上牧场真实后端上报环境读数。

走的是和真实硬件完全相同的链路（X-Device-Key 鉴权 → 引擎去重/防抖 → 存库 → 阈值告警），
只是物理上没有那块电路。适合演示 / 答辩 / 联调。

使用步骤：
  1. 在平台「设备管理 → 添加设备」建一个环境传感器，复制弹出的一次性上行密钥
  2. 把下面 CONFIG 里的 DEVICE_SN / TOPIC 换成你设备实际的信息，KEY 填那把密钥
  3. pip install requests && python virtual_sensor.py
  4. 想停就 Ctrl+C；想看告警效果把 BODY_TEMP_MIN/MAX 调出物种阈值范围（如温度设 40）

真实硬件（ESP32 + DHT22）等价写法见文件底部注释。
"""

import json
import random
import time
from datetime import datetime

import requests

# ====== 按你的实际设备改这三处 ======
CONFIG = {
    "server": "http://43.142.92.229",      # 平台地址（本地调试可改 http://127.0.0.1:8000）
    "key": "把建设备时弹出的上行密钥粘贴到这里",
    "device_sn": "DEV306225",              # 设备编号（建设备时填的）
    "farm_id": 1,                          # 养殖场 ID（设备列表接口里有）
    "pen_id": 1,                           # 绑定圈舍 ID
    "interval_sec": 30,                    # 上报间隔
}

# 基准读数（在阈值内是"正常"；想看告警把基准调出范围即可）
BASE = {"temperature": 22.0, "humidity": 60.0, "nh3": 8.0, "co2": 700.0}
WAVE = {"temperature": 2.5, "humidity": 6.0, "nh3": 2.0, "co2": 150.0}  # 随机波动幅度

URL = f"{CONFIG['server'].rstrip('/')}/api/device/ingest"
TOPIC = f"farm/{CONFIG['farm_id']}/pen/{CONFIG['pen_id']}/{CONFIG['device_sn']}/data"


def build_payload(seq: int) -> dict:
    """读数带正弦日变化 + 随机噪声，比纯随机更像真传感器。"""
    t = time.time()
    metrics = {}
    for k, base in BASE.items():
        wave = WAVE[k]
        drift = wave * 0.5 * ((t / 900) % 2 - 1)              # 15 分钟缓慢起伏
        noise = random.uniform(-wave * 0.15, wave * 0.15)     # 小幅噪声
        metrics[k] = round(base + drift + noise, 2)
    return {
        "msg_id": f"vs-{CONFIG['device_sn']}-{seq}",  # 唯一：QoS1 重发/重放去重靠它
        "device_id": CONFIG["device_sn"],
        "topic": TOPIC,
        "ts": int(t * 1000),
        "metrics": metrics,
    }


def main() -> None:
    key = CONFIG["key"].strip()
    if "粘贴到这里" in key or not key:
        raise SystemExit("先把 CONFIG['key'] 换成建设备时弹出的上行密钥")
    headers = {"X-Device-Key": key, "Content-Type": "application/json"}
    print(f"虚拟传感器启动 → {URL}")
    print(f"  主题: {TOPIC}")
    print(f"  间隔: {CONFIG['interval_sec']}s（Ctrl+C 停止）")
    seq = int(time.time())
    ok = fail = 0
    while True:
        payload = build_payload(seq)
        seq += 1
        try:
            r = requests.post(URL, headers=headers, data=json.dumps(payload), timeout=10)
            body = r.json()
            data = body.get("data") or {}
            if body.get("code") == 0 and (data.get("ok") is not False):
                ok += 1
                print(f"[{datetime.now():%H:%M:%S}] #{seq} 上报成功  "
                      f"温度 {payload['metrics']['temperature']}℃  累计 {ok} 条")
            else:
                fail += 1
                print(f"[{datetime.now():%H:%M:%S}] 上报被拒: {body}（密钥错/设备不存在/topic 不匹配都会这样回）")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"[{datetime.now():%H:%M:%S}] 网络异常: {e}")
        time.sleep(CONFIG["interval_sec"])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止")

"""
===== ESP32 真实硬件等价代码（Arduino C++，DHT22 接 GPIO4）=====
#include <WiFi.h>
#include <HTTPClient.h>
#include <DHT.h>
DHT dht(4, DHT22);
const char* WIFI_SSID = "你家WiFi";
const char* WIFI_PASS = "密码";
const char* URL = "http://43.142.92.229/api/device/ingest";
const char* KEY  = "上行密钥";
const char* SN   = "DEV306225";
const char* TOPIC= "farm/1/pen/1/DEV306225/data";

void setup() {
  Serial.begin(115200); dht.begin();
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(500);
}
void loop() {
  float t = dht.readTemperature(), h = dht.readHumidity();
  if (isnan(t) || isnan(h)) { delay(30000); return; }
  HTTPClient http;
  http.begin(URL);
  http.addHeader("X-Device-Key", KEY);
  http.addHeader("Content-Type", "application/json");
  String body = String("{\"msg_id\":\"") + SN + "-" + millis() +
                "\",\"device_id\":\"" + SN + "\",\"topic\":\"" + TOPIC +
                "\",\"metrics\":{\"temperature\":" + t + ",\"humidity\":" + h + "}}";
  int code = http.POST(body);
  Serial.printf("上报 %d: %.1f℃ %.0f%%\n", code, t, h);
  http.end();
  delay(30000);  // 30 秒一次
}
"""
