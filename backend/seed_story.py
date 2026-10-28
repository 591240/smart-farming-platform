"""三天养殖故事注入（真后端版）— 与前端 mock 的 seedStoryDemoData 同租户、同数值。

用途：切真之后，`docs/22-演示脚本-3天养殖故事.md` 走查手册继续可用。
mock 版走的是浏览器内分发器，本脚本走 HTTP 接口层——同样是"既有能力的编排"，
不新增任何业务分支，演示全程不改代码（PRD 1.4 演示闭环口径）。

运行：
    cd backend && python run.py            # 另开窗口，或用 启动后端.bat
    cd backend && python seed_story.py     # 默认 http://127.0.0.1:8000/api
    python seed_story.py http://127.0.0.1:8001/api

幂等：演示租户已存在即跳过并回登录 token（mock EX-F1 同口径），可反复执行。

登录：手机号 13000000010 / 密码 story123456（与 mock 版同号同密）
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import settings

BASE = (sys.argv[1] if len(sys.argv) > 1 else f"http://{settings.ai_host}:{settings.ai_port}/api").rstrip("/")
# 验证时可换号，避免污染演示租户
PHONE = os.environ.get("STORY_DEMO_PHONE", "13000000010")
PASSWORD = "story123456"

DAY = timedelta(days=1)


def local_date(offset_days: int) -> str:
    """offset_days 天前的本地日期（后端 DATE 字段统一 yyyy-MM-dd）。"""
    return (datetime.now(timezone.utc) + timedelta(days=-offset_days)).date().isoformat()


class Runner:
    def __init__(self) -> None:
        self.c = httpx.Client(base_url=BASE, timeout=90, trust_env=False)
        self.token = ""
        self.notes: list[str] = []
        self.steps: list[str] = []

    def call(self, method: str, path: str, body=None, *, expect_fail: bool = False):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        resp = self.c.request(method, path, json=body, headers=headers)
        data = resp.json()
        if data.get("code") != 0 and not expect_fail:
            raise RuntimeError(f"{method} {path} → {data}")
        return data

    def step(self, text: str) -> None:
        self.steps.append(text)
        print(f"  · {text}")

    def chat(self, message: str, session_id: int | None = None) -> dict:
        """排干 SSE，回最后一个 done 帧的 data（副作用已在图内落库）。"""
        headers = {"Authorization": f"Bearer {self.token}"}
        payload = {"message": message}
        if session_id:
            payload["session_id"] = session_id
        done: dict = {}
        with self.c.stream("POST", "/agent/chat", json=payload, headers=headers) as resp:
            resp.raise_for_status()
            event = ""
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:") and event == "done":
                    import json as _json

                    done = _json.loads(line[5:].strip() or "{}")
        return done


def main() -> None:
    r = Runner()
    print(f"\n=== 三天养殖故事注入（真后端）{BASE} / 租户 {PHONE} ===")

    reg = r.c.post("/auth/register", json={"phone": PHONE, "password": PASSWORD, "nickname": "演示场主"})
    reg_data = reg.json()
    if reg_data.get("code") not in (0, 40901):
        raise SystemExit(f"注册失败：{reg_data}")

    r.token = r.call("POST", "/auth/login", {"phone": PHONE, "password": PASSWORD})["data"]["token"]

    # 幂等：已有场即视为已注入，直接退出（mock EX-F1 同口径）——绝不重复灌第二遍数据
    existing = r.call("GET", "/farm/list")["data"]["items"]
    if existing:
        print(f"  演示数据已就绪（{len(existing)} 个养殖场），跳过重复注入。")
        print(f"  登录：手机号 {PHONE} / 密码 {PASSWORD}")
        print("  如需重灌：先清掉该租户数据（或设 STORY_DEMO_PHONE 用新号验证）。")
        return

    r.call("PUT", "/user/main-species", {"main_species_code": "pig"})

    # —— 场与圈（与 mock 同数值）——
    farm_id = r.call("POST", "/farm", {
        "farm_name": "青禾演示牧场", "province": "湖南省", "city": "长沙市", "county": "长沙县",
        "adcode": "430121", "address": "演示路 88 号", "contact_person": "演", "phone": PHONE,
    })["data"]["farm_id"]
    sow_pen = r.call("POST", "/pen", {
        "farm_id": farm_id, "pen_name": "1号母猪圈", "species_code": "pig",
        "variety_code": "pig_sow", "male_count": 0, "female_count": 3, "batch_stage": None,
    })["data"]["pen_id"]
    fat_pen = r.call("POST", "/pen", {
        "farm_id": farm_id, "pen_name": "2号育肥圈", "species_code": "pig",
        "variety_code": "pig_fattening", "male_count": 20, "female_count": 20, "batch_stage": "fattening",
    })["data"]["pen_id"]
    r.step(f"建场 {farm_id}／母猪圈 {sow_pen}／育肥圈 {fat_pen}（20 公 20 母，初始存栏自动建档耳标）")

    # —— Day 1：传感器超标 → 告警 → 高危问诊 → 用药 ——
    sn = f"SN-STORY-{random.randint(0, 999999)}"
    r.call("POST", "/device", {"device_name": "2号育肥圈温湿度", "device_type": "sensor", "device_sn": sn, "pen_id": fat_pen})
    alerts_before = r.call("GET", "/alert/list")["data"]["total"]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for i in range(3):  # 连续 3 次超标才出告警（防抖 3 次，07 协议）
        # ts 刻意留在 90s 心跳窗口内：后端用消息 ts 记心跳，若按"Day1=2 天前"发历史 ts，
        # 设备会被立即判离线并多出一条 device_offline 告警（补报判定基准未定，见 08 文档 C-09）。
        r.call("POST", "/device/ingest", {
            "msg_id": f"story-d1-{sn}-{i}", "device_id": sn,
            "topic": f"farm/{farm_id}/pen/{fat_pen}/{sn}/data",
            "ts": now_ms - i * 20_000,
            "metrics": {"temperature": 35 + i * 0.2},
        })
    alerts_after = r.call("GET", "/alert/list")["data"]["total"]
    if alerts_after <= alerts_before:
        raise SystemExit(
            f"Day1 传感器告警未生成（{alerts_before} → {alerts_after}）。"
            "合并窗口键是 `sf:merge:sensor:{pen_id}`，TTL 1h，现在落在 Redis 里——"
            "**重启后端不再清掉它**。清掉再重灌：\n"
            "  docker exec sf-redis redis-cli --scan --pattern 'sf:merge:*' | "
            "xargs -r -n50 docker exec -i sf-redis redis-cli del\n"
            "（若 REDIS_URL 留空，窗口仍在服务进程内存，重启后端即可清掉。）"
        )
    r.step(f"Day1 传感器 3 次超标注入 → 告警 {alerts_before} → {alerts_after}")

    done = r.chat("2号育肥圈猪群发烧41度，呼吸困难，有几头犬坐姿势，皮肤发紫")
    if done.get("risk_level") != "high" or not done.get("vet_review_created"):
        raise SystemExit(f"Day1 高危问诊未按预期挂起：{done}")
    r.step(f"Day1 高危问诊 → 会话#{done['session_id']} 已挂起在 hitl_gate，复核单 pending")

    meds = r.call("GET", "/medicine/list")["data"]["items"]
    flu = next((m for m in meds if "氟苯尼考" in m["med_name"]), None)
    if flu:
        r.call("POST", "/medicine/usage", {
            "pen_id": fat_pen, "med_id": flu["med_id"], "usage_date": local_date(2),
            "dosage": "按说明书肌注", "operator_note": "胸膜肺炎治疗（兽医复核后方案）",
        })
        r.step(f"Day1 用药登记：{flu['med_name']}（末次用药 {local_date(2)}，休药期为出栏拦截埋点）")

    # —— Day 2：配种 → 妊检 → 分娩活仔 11 ——
    female_id = r.call("POST", "/breeding-female", {"pen_id": sow_pen, "tag_no": "S-01", "parity": 2})["data"]["female_id"]
    r.call("POST", "/lifecycle/event", {"event_type": "breeding", "female_id": female_id, "event_date": local_date(116)})
    r.call("POST", "/lifecycle/event", {"event_type": "pregnancy_check", "female_id": female_id, "detail": {"result": "阳性"}})
    r.call("POST", "/lifecycle/event", {
        "event_type": "birth", "female_id": female_id,
        "event_date": local_date(1), "detail": {"live_male": 5, "live_female": 6, "dead_count": 1},
    })
    r.step("Day2 母畜 S-01：配种(116 天前) → 妊检阳性 → 分娩活仔 11（仔畜计入母猪所在圈）")

    # —— Day 3 前置：称重两次 + 连续 12 天饲喂登记（让日增重、采食曲线、料肉比都有真数据）——
    r.call("POST", "/feeding/weight", {"pen_id": fat_pen, "weigh_date": local_date(30), "avg_weight_kg": 60, "sample_count": 5})
    r.call("POST", "/feeding/weight", {"pen_id": fat_pen, "weigh_date": local_date(16), "avg_weight_kg": 71, "sample_count": 5})
    for d in range(13, 1, -1):  # 12 天 × ~130kg：出栏时料肉比落在可信区间（≈3.4）
        r.call("POST", "/feeding/record", {
            "pen_id": fat_pen, "feed_date": local_date(d), "feed_type": "concentrate",
            "amount_kg": 128 + (d % 3) * 4, "remark": "按计划执行",
        })
    r.step("Day3 前置：称重两次（60→71kg，日增重 0.786 vs 标准 0.75）+ 连续 12 天饲喂登记")

    # —— Day 3：出栏休药期拦截 → 强制留痕 ——
    blocked = r.call("POST", f"/pen/{fat_pen}/slaughter", {"male_count": 5, "female_count": 5, "avg_weight_kg": 110}, expect_fail=True)
    if blocked.get("code") != 40001:
        raise SystemExit(f"Day3 出栏未按预期拦截休药期：{blocked}")
    info = blocked.get("data") or {}
    r.step(f"Day3 出栏被休药期拦截（40001）：{info.get('med_name', '—')} 剩余 {info.get('left_days', '—')} 天")

    tags = r.call("GET", f"/individual/list?pen_id={fat_pen}&status=active&page_size=50")["data"]["items"]
    male_tags = [t["ear_tag_no"] for t in tags if t["gender"] == "male"][:5]
    female_tags = [t["ear_tag_no"] for t in tags if t["gender"] == "female"][:5]
    if len(male_tags) < 5 or len(female_tags) < 5:
        raise SystemExit(f"Day3 在栏耳标不足：男 {male_tags} 女 {female_tags}")
    r.call("POST", f"/pen/{fat_pen}/slaughter", {
        "male_count": 5, "female_count": 5, "avg_weight_kg": 110, "destination": "演示屠宰点",
        "force": True, "force_note": "已告知休药期要求，自行承担合规责任（演示）",
        "tag_nos": male_tags + female_tags,
    })
    pens = r.call("GET", f"/pen/list?farm_id={farm_id}")["data"]["items"]
    fat_now = next((p for p in pens if p["pen_id"] == fat_pen), {})
    r.step(
        f"Day3 强制出栏留痕成功（10 头，台账 slaughter 带 force_note），"
        f"育肥圈存栏 → 公 {fat_now.get('male_count', '?')} / 母 {fat_now.get('female_count', '?')}"
    )

    # —— 仍是 mock 的域：如实标注，不假装成功 ——
    r.notes.append("视觉事件 /vision/events 与 /vision/simulate 未在真后端实装，"
                   "Day1 走查里的「视觉告警」这一项在切真账号下看不到（走 YOLO 边缘网关时才有数据）")
    r.notes.append("知识库阅读页 /kb/list 与后台 /admin/kb、3D 场景 /scene/analyze-photo 未切真，"
                   "在真后端账号下为空（属 C4 批次）")
    r.notes.append("看板聚合 /dashboard/summary 未实装（C4），首页部分卡片在切真账号下为空")

    print("\n=== 走查提示 ===")
    print(f"  登录：手机号 {PHONE} / 密码 {PASSWORD}")
    print("  Day1 告警中心应见 1 条 high 传感器告警；AI 问诊见已挂起病例；")
    print("  兽医复核页需 admin 建兽医号（/admin/user）后登录复核，复核通过会落 vet_review 台账；")
    print("  Day3 出栏登记：先见 40001 红卡拦截，勾选强制继续填备注后放行。")
    for n in r.notes:
        print(f"  ⚠ {n}")
    print(f"\n完成：{len(r.steps)} 个注入步骤全部成功。\n")


if __name__ == "__main__":
    main()
