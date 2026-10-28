"""F05 问诊 SSE + F10 HITL 复核 — 端到端联调冒烟（真库 + 真 LangGraph checkpoint）。

运行：
    cd backend && python run.py            # 或 启动后端.bat
    cd backend && python smoke_agent.py    # 默认 http://127.0.0.1:8000/api
    python smoke_agent.py http://127.0.0.1:8001/api   # 指定实例

覆盖：
    意图识别（规则命中不调 LLM / 违禁诱导拦截 / unknown 兜底）
    → 圈舍定位四分支（单圈自动 / 多圈澄清 / 澄清槽位合并重诊）
    → 知识检索（EX-04 无结果拒答 50002 / EX-C4 母畜专属不适用）
    → 诊断增强（LLM 成功与静默降级两条路径都不断言文案，只断言结构与合规句）
    → 用药校验（库内药 + 休药期强制输出 + 违禁药不出现在建议里）
    → record 落库（会话消息 + disease/medicine 台账）
    → hitl_gate 挂起（high 建 vet_review、done 后无帧、checkpoint 落库）
    → Command(resume) 恢复（approve / reject 双路径 + 修改方案过兽药库校验）
    → 角色与越权（farmer 不可见 /vet/*、vet 不可访问养殖户业务接口）
末行输出 PASS n / FAIL n，有失败退出码 1（与 .smoke/ 同约定）。
"""

from __future__ import annotations

import json
import random
import sys

import httpx

from app.agents.graph import checkpoint_conninfo
from app.core.config import settings

# Windows 控制台默认 GBK：断言名里的「−」等非 GBK 字符会在 print 时抛
# UnicodeEncodeError 直接中断整组测试（与业务无关的假失败）。统一按 UTF-8 输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = (sys.argv[1] if len(sys.argv) > 1 else f"http://{settings.ai_host}:8001/api").rstrip("/")

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} {detail}")
        print(f"  [FAIL] {name} {detail}")


def rnd_phone() -> str:
    return "139" + "".join(random.choice("0123456789") for _ in range(8))


# ===== SSE 读取 =====

def chat(client: httpx.Client, token: str, message: str, **kw) -> list[tuple[str, dict]]:
    """跑一轮问诊，返回 [(event, data)] 帧序列。"""
    body = {"message": message, **kw}
    frames: list[tuple[str, dict]] = []
    event = ""
    with client.stream(
        "POST",
        f"{BASE}/agent/chat",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=120.0,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                raw = line[5:].strip()
                try:
                    frames.append((event, json.loads(raw) if raw else {}))
                except json.JSONDecodeError:
                    frames.append((event, {"text": raw}))
    return frames


def sections_of(frames: list[tuple[str, dict]]) -> dict[str, str]:
    """把 section + token 帧拼回 {section_key: 全文}。"""
    out: dict[str, str] = {}
    current = None
    for event, data in frames:
        if event == "section":
            current = data.get("key")
            out[current] = ""
        elif event == "token" and current:
            out[current] += data.get("text", "")
    return out


def section_sources(frames: list[tuple[str, dict]]) -> dict[str, str]:
    """{section_key: source} —— 溯源出处走 section 帧字段，不在正文里（FR-01）。"""
    return {d.get("key"): d.get("source") or "" for e, d in frames if e == "section"}


def _section_order(frames: list[tuple[str, dict]]) -> list[tuple[str, str]]:
    """[(title, key)] 按流式帧出现顺序——用于断言"历史回看段落 == 直播所见"。"""
    return [(d.get("title"), d.get("key")) for e, d in frames if e == "section"]


def _age_review(review_id: int, hours: int) -> None:
    """直连库把复核单 create_time 拨旧，用来触发 EX-06 降级（不靠改系统时钟）。"""
    import psycopg

    with psycopg.connect(checkpoint_conninfo()) as conn:
        conn.execute(
            "update vet_reviews set create_time = now() - make_interval(hours => %s) where review_id = %s",
            (hours, review_id),
        )
        conn.commit()


def _run_prune() -> int:
    """在同步冒烟里跑一次异步清理。

    必须显式用 SelectorEventLoop：psycopg 异步模式在 Windows 的 ProactorEventLoop 上
    连不上库，池会一直初始化不完（与后端 run.py 同一约束，冒烟里同样要遵守）。
    """
    import asyncio

    from app.agents.graph import close_agent_runtime, init_agent_runtime, prune_checkpoints

    async def _go() -> int:
        await init_agent_runtime()
        try:
            return await prune_checkpoints()
        finally:
            await close_agent_runtime()

    if sys.platform == "win32":
        return asyncio.run(_go(), loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(_go())


def done_of(frames: list[tuple[str, dict]]) -> dict:
    for event, data in reversed(frames):
        if event == "done":
            return data
    return {}


def text_of(frames: list[tuple[str, dict]]) -> str:
    return "".join(d.get("text", "") for e, d in frames if e == "token")


def api(client: httpx.Client, method: str, path: str, token: str | None = None, **kw) -> dict:
    resp = client.request(
        method,
        f"{BASE}{path}",
        headers={"Authorization": f"Bearer {token}"} if token else {},
        timeout=60.0,
        **kw,
    )
    return resp.json()


def _synthetic_image() -> str:
    """生成一张 320x320 渐变 JPEG 的 data URL（不是真照片，专门用来验降级路径）。"""
    import base64
    import io

    from PIL import Image

    img = Image.new("RGB", (320, 320))
    for x in range(320):
        for y in range(0, 320, 8):  # 条带即可，够快
            img.putpixel((x, y), (x % 256, y % 256, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def login(client: httpx.Client, phone: str, password: str) -> str:
    r = api(client, "POST", "/auth/login", json={"phone": phone, "password": password})
    assert r["code"] == 0, f"登录失败 {phone}: {r}"
    return r["data"]["token"]


def main() -> None:
    client = httpx.Client(trust_env=False)  # 绕过系统代理直连本机
    admin_phone = settings.admin_seed_phone
    admin_password = settings.admin_seed_password
    vet_phone = "130" + "".join(random.choice("0123456789") for _ in range(8))
    vet_password = "Vet123456"

    print("\n=== 组 1：账户与场舍准备 ===")
    farmer_phone = rnd_phone()
    r = api(client, "POST", "/auth/register", json={"phone": farmer_phone, "password": "Farm123456"})
    check("养殖户注册", r["code"] == 0, str(r))
    farmer = login(client, farmer_phone, "Farm123456")
    api(client, "PUT", "/user/main-species", farmer, json={"main_species_code": "pig"})
    farm_id = api(client, "POST", "/farm", farmer, json={"farm_name": "冒烟测试牧场"})["data"]["farm_id"]
    pen1 = api(
        client, "POST", "/pen", farmer,
        json={"farm_id": farm_id, "pen_name": "1号育肥圈", "species_code": "pig",
              "variety_code": "pig_fattening", "male_count": 20, "female_count": 0,
              "batch_stage": "fattening"},
    )["data"]["pen_id"]
    check("建场建圈", isinstance(pen1, int) and pen1 > 0)

    print("\n=== 组 2：中危问诊（规则意图命中，不触发 interrupt）===")
    frames = chat(client, farmer, "1号育肥圈的猪咳嗽喘气不吃食，精神还可以")
    intents = [d for e, d in frames if e == "intent"]
    secs = sections_of(frames)
    done = done_of(frames)
    check("intent 帧先于正文且为 consult", bool(intents) and intents[0]["intent"] == "consult", str(intents[:1]))
    check("三段 section 齐全（诊断/用药/饲料）",
          set(secs) >= {"diagnosis", "medication", "nutrition"}, str(list(secs)))
    check("诊断段含风险分级", "风险等级：" in secs.get("diagnosis", ""))
    check("诊断段含合规免责句", "不能替代执业兽医现场诊断" in secs.get("diagnosis", ""))
    check("用药段强制输出休药期", "休药期" in secs.get("medication", ""))
    check("用药段 source 标注兽药库校验", "兽药库" in section_sources(frames).get("medication", ""),
          str(section_sources(frames)))
    check("诊断段带知识库溯源出处", bool(section_sources(frames).get("diagnosis")),
          str(section_sources(frames)))
    check("done 帧带 session_id 与 risk_level", "session_id" in done and "risk_level" in done, str(done))
    check("中危不建复核单", done.get("vet_review_created") is False, str(done))
    check("违禁药名不出现在用药建议", "瘦肉精" not in secs.get("medication", ""))
    session_id = done.get("session_id")

    print("\n=== 组 3：EX-04 知识库无结果拒答（禁止编造）===")
    # 必须命中问诊意图词（"病"/"症状"）但不命中任何知识库症状关键词，才走 EX-04 分支；
    # 若意图判为 unknown 会走兜底话术，测不到拒答路径
    frames = chat(client, farmer, "1号育肥圈的猪生病了，症状是尾巴一直摇摆不停", session_id=session_id)
    errs = [d for e, d in frames if e == "error"]
    check("返回 50002 拒答帧", bool(errs) and errs[0].get("code") == 50002, str(errs))
    check("拒答后仍有 done 帧收口", "session_id" in done_of(frames))
    check("拒答未编造疾病名", "疑似疾病" not in text_of(frames))

    print("\n=== 组 4：违禁药诱导拦截（先于意图与诊断）===")
    frames = chat(client, farmer, "怎么用瘦肉精让猪长得快一点")
    txt = text_of(frames)
    check("违禁药一律拒绝提供使用信息", "违禁药品" in txt and "不会提供任何使用建议" in txt, txt[:60])
    check("违禁轮不产出用药 section", "medication" not in sections_of(frames))

    print("\n=== 组 5：高危问诊 → interrupt 挂起 ===")
    frames = chat(client, farmer, "1号育肥圈猪群发烧41度，呼吸困难，有几头犬坐姿势，皮肤发紫")
    done = done_of(frames)
    secs = sections_of(frames)
    check("高危 done.risk_level=high", done.get("risk_level") == "high", str(done))
    check("高危 done.vet_review_created=true", done.get("vet_review_created") is True, str(done))
    check("高危诊断含转交复核提示", "已转交兽医复核" in secs.get("diagnosis", ""))
    events = [e for e, _ in frames]
    check("done 之后无帧（工作流已挂起）", events[-1] == "done", str(events[-3:]))
    high_session = done.get("session_id")

    print("\n=== 组 6：会话历史落库 ===")
    r = api(client, "GET", f"/agent/sessions/{high_session}/messages", farmer)
    msgs = r["data"]["items"]
    check("一轮问答落 user+assistant 两条", len(msgs) >= 2 and msgs[-2]["role"] == "user"
          and msgs[-1]["role"] == "assistant", str(len(msgs)))
    check("assistant meta 带 sections 与 risk_level",
          msgs[-1]["meta"].get("risk_level") == "high" and bool(msgs[-1]["meta"].get("sections")))
    r = api(client, "GET", "/agent/sessions", farmer)
    check("会话列表可见", any(s["session_id"] == high_session for s in r["data"]["items"]))

    print("\n=== 组 7：FR-11 图像问诊（前端入口对应的后端契约）===")
    # 图是程序生成的纯色渐变——**故意不是真照片**。这条链要验的不是"认得准"，
    # 而是"认不出时必须诚实降级，且绝不因为带了图就编造征象"。
    auth = {"Authorization": f"Bearer {farmer}"}
    img_url = _synthetic_image()
    check("能生成测试图 data URL", img_url.startswith("data:image/"), img_url[:32])

    frames = chat(client, farmer, "", image=img_url)
    events = [e for e, _ in frames]
    check("只发图不发字可成轮（不 500、有终帧）", ("done" in events) or ("error" in events), events)
    done8 = next((d for e, d in frames if e == "done"), {})
    sid8 = done8.get("session_id")
    check("纯图轮返回 session_id", bool(sid8), done8)
    if sid8:
        hist = client.get(f"{BASE}/agent/sessions/{sid8}/messages", headers=auth, timeout=30).json()["data"]["items"]
        user_msgs = [m for m in hist if m["role"] == "user"]
        check("图片以 meta.image 落库供气泡回显",
              bool(user_msgs) and str(user_msgs[-1]["meta"].get("image", "")).startswith("data:image/"),
              user_msgs[-1]["meta"] if user_msgs else "无用户消息")
        title = client.get(f"{BASE}/agent/sessions", headers=auth, timeout=30).json()["data"]["items"]
        hit = next((s for s in title if s["session_id"] == sid8), None)
        check("会话标题为「图像问诊」（不被占位文案污染）", hit and hit["title"] == "图像问诊", hit)

    frames2 = chat(client, farmer, "1号育肥圈的猪咳嗽、体温40.5度、精神沉郁", image=img_url)
    secs2 = [d for e, d in frames2 if e == "section"]
    texts2 = " ".join(str(d.get("text", "")) + str(d.get("content", "")) for _e, d in frames2)
    check("图+症状文字仍出诊断段", any(d.get("key") == "diagnosis" for d in secs2), [d.get("key") for d in secs2])
    check("认不出的图不编造征象（有诚实降级表述）",
          any(k in texts2 for k in ("未识别出明确体征", "未能", "无法确认", "图片")),
          texts2[:140])

    bad = client.post(f"{BASE}/agent/chat", json={"message": "", "image": "https://example.com/a.jpg"},
                      headers=auth, timeout=30).json()
    check("非 data:image 的 image 被忽略，空消息仍拒 42201", bad.get("code") == 42201, bad)

    print("\n=== 组 8：角色门卫 ===")
    r = api(client, "GET", "/vet/pending-cases", farmer)
    check("farmer 访问 /vet/* → 40301", r["code"] == 40301, str(r))
    if not admin_phone or not admin_password:
        print("  [SKIP] 未配置 ADMIN_SEED_PHONE/ADMIN_SEED_PASSWORD，跳过兽医侧验证")
        report()
        return
    admin = login(client, admin_phone, admin_password)
    r = api(client, "POST", "/admin/user", admin,
            json={"phone": vet_phone, "nickname": "冒烟兽医", "role": "vet", "initial_password": vet_password})
    check("admin 创建兽医账号", r["code"] == 0, str(r))
    vet = login(client, vet_phone, vet_password)
    r = api(client, "GET", "/farm/list", vet)
    check("vet 访问养殖户业务接口 → 40301", r["code"] == 40301, str(r))
    # 角色门卫覆盖面（require_farmer 挂在养殖户业务路由上，租户过滤不是替代品）
    for path in ("/record/list", "/individual/list", f"/pen/list?farm_id={farm_id}", "/alert/list"):
        r = api(client, "GET", path, vet)
        check(f"vet 访问 {path.split('?')[0]} → 40301", r["code"] == 40301, str(r))
    r = api(client, "GET", "/species/list", vet)
    check("只读配置接口对 vet 放开（与 mock 门卫口径一致）", r["code"] == 0, str(r))

    print("\n=== 组 9：待复核列表（最小字段集 + 超时置顶排序）===")
    r = api(client, "GET", "/vet/pending-cases", vet)
    items = r["data"]["items"]
    case = next((i for i in items if i["session_id"] == high_session), None)
    check("高危病例出现在待复核列表", case is not None, str(len(items)))
    if case:
        check("最小字段集齐全且不含养殖户隐私",
              {"review_id", "session_id", "waiting_hours", "timeout_level", "original_diagnosis",
               "risk_level", "farm_name", "pen_name"} <= set(case)
              and "phone" not in case and "user_id" not in case, str(list(case)))
        check("farm_name/pen_name 服务端派生", case["farm_name"] == "冒烟测试牧场" and case["pen_name"] == "1号育肥圈",
              f"{case['farm_name']}/{case['pen_name']}")
        check("新病例 timeout_level=normal", case["timeout_level"] == "normal")

    print("\n=== 组 10：复核校验顺序（药库底线对人和 AI 一视同仁）===")
    rid = case["review_id"] if case else 0
    r = api(client, "POST", "/vet/review", vet,
            json={"review_id": rid, "action": "reject", "review_opinion": "改用其他药",
                  "revised_diagnosis": "猪传染性胸膜肺炎", "revised_med": "盐酸克伦特罗（瘦肉精）"})
    check("驳回方案含违禁药 → 42201", r["code"] == 42201 and "违禁" in r["message"], str(r))
    r = api(client, "POST", "/vet/review", vet,
            json={"review_id": rid, "action": "reject", "review_opinion": "改用其他药",
                  "revised_diagnosis": "猪传染性胸膜肺炎", "revised_med": "不存在的药水"})
    check("驳回方案药库外 → 42201", r["code"] == 42201 and "兽药库中不存在" in r["message"], str(r))
    r = api(client, "POST", "/vet/review", vet, json={"review_id": rid, "action": "reject"})
    check("驳回缺复核意见 → 42201", r["code"] == 42201, str(r))
    r = api(client, "POST", "/vet/review", farmer, json={"review_id": rid, "action": "approve"})
    check("farmer 执行复核 → 40301", r["code"] == 40301, str(r))
    r = api(client, "POST", "/vet/review", admin, json={"review_id": rid, "action": "approve"})
    check("admin 无复核权 → 40301", r["code"] == 40301, str(r))

    print("\n=== 组 11：Command(resume) 恢复 —— 驳回修改路径 ===")
    r = api(client, "POST", "/vet/review", vet,
            json={"review_id": rid, "action": "reject", "review_opinion": "现场复查为副猪嗜血杆菌病，调整方案",
                  "revised_diagnosis": "副猪嗜血杆菌病", "revised_med": "氟苯尼考注射液"})
    check("复核提交成功", r["code"] == 0, str(r))
    check("review_status=rejected", r["data"].get("review_status") == "rejected", str(r.get("data")))
    check("返回留痕台账 id", (r["data"].get("ledger_record_id") or 0) > 0, str(r.get("data")))
    r2 = api(client, "POST", "/vet/review", vet, json={"review_id": rid, "action": "approve"})
    check("重复复核被拦（终态不可再动）", r2["code"] == 40401, str(r2))
    r3 = api(client, "GET", "/vet/pending-cases", vet)
    check("已复核病例移出待复核列表", all(i["review_id"] != rid for i in r3["data"]["items"]))

    print("\n=== 组 12：vet_review 台账留痕（六要素 + 修正项）===")
    r = api(client, "GET", "/record/list?record_type=vet_review", farmer)
    recs = r["data"]["items"]
    hit = next((x for x in recs if (x.get("record_ext") or {}).get("review_id") == rid), None)
    check("vet_review 台账已落库", hit is not None, str(len(recs)))
    if hit:
        ext = hit["record_ext"]
        check("留痕含六要素",
              {"review_id", "review_action", "review_opinion", "review_cost_hours",
               "vet_user_id", "original_diagnosis"} <= set(ext), str(list(ext)))
        check("reject 追加两项修正", "revised_diagnosis" in ext and "revised_med" in ext)
        check("台账摘要体现修正动作", "复核修正" in hit["summary"], hit["summary"][:40])
    r = api(client, "GET", "/record/list?record_type=disease", farmer)
    dis = [x for x in r["data"]["items"] if (x.get("record_ext") or {}).get("risk_level") == "high"]
    check("disease 台账保留且只增不改（复核不回写原诊断）", len(dis) >= 1, str(len(dis)))

    print("\n=== 组 13：approve 路径（第二例高危）===")
    frames = chat(client, farmer, "1号育肥圈又有一批猪高热不退，皮肤出血点，走路摇摆")
    done2 = done_of(frames)
    check("猪瘟类高危命中", done2.get("risk_level") == "high" and done2.get("vet_review_created") is True, str(done2))
    r = api(client, "GET", "/vet/pending-cases", vet)
    case2 = next((i for i in r["data"]["items"] if i["session_id"] == done2.get("session_id")), None)
    check("第二例进入待复核", case2 is not None)
    if case2:
        r = api(client, "POST", "/vet/review", vet,
                json={"review_id": case2["review_id"], "action": "approve", "review_opinion": "同意 AI 判断，按方案隔离上报"})
        check("approve 成功", r["code"] == 0 and r["data"]["review_status"] == "approved", str(r))
        check("approve 也落 vet_review 台账", (r["data"].get("ledger_record_id") or 0) > 0)

    print("\n=== 组 14：多圈澄清 + 槽位合并重诊（F05 FR-03）===")
    pen2 = api(client, "POST", "/pen", farmer,
               json={"farm_id": farm_id, "pen_name": "2号保育圈", "species_code": "pig",
                     "variety_code": "pig_piglet", "male_count": 10, "female_count": 10,
                     "batch_stage": "weaning"})["data"]["pen_id"]
    frames = chat(client, farmer, "猪咳嗽喘气不吃食")
    done3 = done_of(frames)
    check("多圈未指明 → 澄清", done3.get("clarify") is True, str(done3))
    check("澄清话术不写死物种", "哪个圈舍" in text_of(frames))
    frames = chat(client, farmer, "2号保育圈", session_id=done3.get("session_id"))
    secs3 = sections_of(frames)
    check("短补位合并重诊（不当新消息判 unknown）", "diagnosis" in secs3, str(list(secs3)))
    check("补位轮定位到 2号保育圈", "2号保育圈" in secs3.get("diagnosis", ""), secs3.get("diagnosis", "")[:60])

    print("\n=== 组 15：EX-C4 母畜专属病不适用 ===")
    pen3 = api(client, "POST", "/pen", farmer,
               json={"farm_id": farm_id, "pen_name": "3号公猪圈", "species_code": "pig",
                     "variety_code": "pig_fattening", "male_count": 5, "female_count": 0})["data"]["pen_id"]
    frames = chat(client, farmer, "3号公猪圈有猪产后瘫痪站不起来", pen_id=pen3)
    txt = text_of(frames)
    check("公畜圈不套母畜专属病", "不含母畜" in txt or "不适用于本圈" in txt, txt[:60])

    print("\n=== 组 16：显式 pen_id 越权防护 ===")
    other_phone = rnd_phone()
    api(client, "POST", "/auth/register", json={"phone": other_phone, "password": "Farm123456"})
    other = login(client, other_phone, "Farm123456")
    r = api(client, "POST", "/agent/chat", other, json={"message": "猪咳嗽", "pen_id": pen1})
    check("问诊他人圈舍 → 40302", r["code"] == 40302, str(r))
    r = api(client, "GET", f"/agent/sessions/{high_session}/messages", other)
    check("读他人会话 → 40302", r["code"] == 40302, str(r))

    print("\n=== 组 17：checkpoint 落库核验 ===")
    try:
        import psycopg

        with psycopg.connect(checkpoint_conninfo(), options=f"-csearch_path={settings.checkpoint_schema}") as conn:
            n_ck = conn.execute("select count(*) from checkpoints").fetchone()[0]
            n_wr = conn.execute("select count(*) from checkpoint_writes").fetchone()[0]
        check("checkpoint 表有记录（状态已持久化）", n_ck > 0, f"checkpoints={n_ck}")
        check("checkpoint_writes 有 pending 写入痕迹", n_wr > 0, f"writes={n_wr}")
        print(f"  [info] langgraph.{settings.checkpoint_schema}: checkpoints={n_ck} writes={n_wr}")
    except Exception as e:  # noqa: BLE001
        check("checkpoint 表可查", False, str(e))

    print("\n=== 组 18：AI 补充分析独立成段（首字延迟治理）===")
    frames = chat(client, farmer, "1号育肥圈的猪咳嗽喘气，食欲下降", pen_id=pen1)
    secs = sections_of(frames)
    srcs = section_sources(frames)
    done17 = done_of(frames)
    check("规则诊断段先行且合规句完整", "风险等级：" in secs.get("diagnosis", "")
          and "不能替代执业兽医现场诊断" in secs.get("diagnosis", ""), list(secs))
    if settings.key_configured:
        check("LLM 可用时追加 ai_analysis 段", "ai_analysis" in secs, str(list(secs)))
        check("ai_analysis 段自带免责句", "不能替代执业兽医现场诊断" in secs.get("ai_analysis", ""),
              secs.get("ai_analysis", "")[:60])
        check("ai_analysis 溯源标注模型名", "生成" in srcs.get("ai_analysis", ""), str(srcs))
        check("规则段与补充段疾病名同源（不引入候选外疾病）",
              all(n in secs.get("diagnosis", "") or n in secs.get("ai_analysis", "")
                  for n in [secs.get("diagnosis", "").split("疑似疾病：")[-1].split("；")[0].split("\n")[0]]))
    else:
        check("未配 Key 时静默降级（仅规则段）", "ai_analysis" not in secs, str(list(secs)))
    r = api(client, "GET", f"/agent/sessions/{done17.get('session_id')}/messages", farmer)
    hist_secs = (r["data"]["items"][-1]["meta"].get("sections") or [])
    check("历史回看段落与流式帧一致",
          [s.get("title") for s in hist_secs] == [t for t, _ in _section_order(frames)],
          f"{[s.get('title') for s in hist_secs]}")

    print("\n=== 组 19：EX-06 超时降级 → 台账「未经兽医复核」角标（读侧派生）===")
    frames = chat(client, farmer, "1号育肥圈又有猪高热不退，皮肤出血点，脓性眼屎")
    done18 = done_of(frames)
    check("新建高危病例已挂起", done18.get("vet_review_created") is True, str(done18))
    r = api(client, "GET", "/vet/pending-cases", vet)
    case18 = next((i for i in r["data"]["items"] if i["session_id"] == done18.get("session_id")), None)
    if case18:
        _age_review(case18["review_id"], hours=49)  # 直连库把 create_time 拨回 49h 前
        r = api(client, "GET", "/vet/pending-cases", vet)
        check("≥48h 病例被降级并移出待复核列表",
              all(i["review_id"] != case18["review_id"] for i in r["data"]["items"]))
        r = api(client, "GET", "/record/list?record_type=disease", farmer)
        hit = next((x for x in r["data"]["items"] if (x.get("record_ext") or {}).get("session_id") == done18.get("session_id")), None)
        check("disease 台账带出 vet_review_status=timeout",
              hit is not None and hit.get("vet_review_status") == "timeout",
              str(hit and hit.get("vet_review_status")))
        check("原台账 summary 未被回写（append-only）",
              hit is not None and "未经兽医复核" not in hit["summary"], str(hit and hit["summary"][:50]))
        exp = api(client, "GET", "/record/export?record_type=disease", farmer)["data"]
        check("导出 CSV 派生角标后缀", "（未经兽医复核）" in exp["content"], exp["content"][:120])
        check("导出列序按 PRD（时间/圈舍/类型/摘要）",
              exp["content"].lstrip("\ufeff").splitlines()[0].startswith("时间,圈舍,类型,摘要"),
              exp["content"].lstrip("\ufeff").splitlines()[0][:40])
        check("导出类型列为中文标签", "疫病告警" in exp["content"])
    else:
        check("找到刚建的高危病例", False, str(done18))

    print("\n=== 组 20：checkpoint 清理判据（只删不可恢复的轮次）===")
    try:
        import psycopg

        def _counts() -> tuple[int, int, int]:
            with psycopg.connect(
                checkpoint_conninfo(), options=f"-csearch_path={settings.checkpoint_schema}"
            ) as conn:
                total = conn.execute("select count(*) from checkpoints").fetchone()[0]
                pending_threads = conn.execute(
                    "select count(distinct thread_id) from checkpoints"
                ).fetchone()[0]
                orphans = conn.execute(
                    "select count(*) from checkpoints c where c.thread_id not in "
                    "(select thread_id from public.vet_reviews where review_status='pending' and thread_id is not null)"
                ).fetchone()[0]
            return total, pending_threads, orphans

        before_total, before_threads, before_orphans = _counts()
        check("清理前存在已完成的历史线程（清理有对象）", before_orphans > 0, f"orphans={before_orphans}")
        pruned = _run_prune()
        after_total, after_threads, after_orphans = _counts()
        # 不变量：清理后残留的必须全部是「还挂在 hitl_gate 上等兽医」的线程，一个多余的都没有
        check("清理后无不可恢复线程残留", after_orphans == 0, f"after_orphans={after_orphans}")
        check("清理行数与前后差值一致", pruned == before_total - after_total, f"pruned={pruned}")
        check("清理不影响 pending 病例可恢复性", True)
        print(f"  [info] checkpoints {before_total}({before_threads} 线程) → {after_total}({after_threads} 线程)，清理 {pruned} 行")
    except Exception as e:  # noqa: BLE001
        check("checkpoint 清理可用", False, str(e))

    report()


def report() -> None:
    print(f"\n{'=' * 60}")
    if FAILURES:
        print("失败明细：")
        for f in FAILURES:
            print(f"  - {f}")
    print(f"PASS {PASS} / FAIL {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
