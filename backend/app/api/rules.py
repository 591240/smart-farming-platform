"""业务规则常量 — 与前端 species-data.ts / mock-backend.ts 逐值对齐（切真口径一致）。

- METRIC_META / SPECIES_ENV_THRESHOLDS：F03 传感器指标与物种环境阈值（02-数据库 5.1）
- LIFECYCLE_PARAMS：F07 物种生命周期参数（PRD 附录 D 口径，估算值）
- MEDICINE_LIB_SEED：兽药库种子（02-数据库 3.6；公开兽药标签用法示例整理）
- CLIMATE_RISK_SEED / RISK_COPY：F08 气候风险规则与文案（02-数据库 5.1）
"""

from typing import Any

from app.core.config import settings

METRIC_META: dict[str, dict[str, str]] = {
    "temperature": {"label": "温度", "unit": "℃"},
    "humidity": {"label": "湿度", "unit": "%RH"},
    "nh3": {"label": "氨气", "unit": "ppm"},
    "co2": {"label": "二氧化碳", "unit": "ppm"},
}
VALID_METRICS = set(METRIC_META)

SPECIES_ENV_THRESHOLDS: dict[str, dict[str, dict[str, float]]] = {
    "pig": {
        "temperature": {"min": 16, "max": 28, "criticalMin": 10, "criticalMax": 33},
        "humidity": {"min": 50, "max": 80},
        "nh3": {"max": 25, "criticalMax": 50},
        "co2": {"max": 3000, "criticalMax": 5000},
    },
    # F12 FR-05：羊/牛环境阈值（02-数据库 5.1 口径外推，估算值；切真后按物种灌库精调）
    "sheep": {
        "temperature": {"min": 10, "max": 25, "criticalMin": 5, "criticalMax": 30},
        "humidity": {"min": 50, "max": 80},
        "nh3": {"max": 20, "criticalMax": 40},
        "co2": {"max": 3000, "criticalMax": 5000},
    },
    "cattle": {
        "temperature": {"min": 8, "max": 26, "criticalMin": 2, "criticalMax": 32},
        "humidity": {"min": 50, "max": 85},
        "nh3": {"max": 20, "criticalMax": 40},
        "co2": {"max": 3000, "criticalMax": 5000},
    },
}


def env_thresholds(species_code: str | None) -> dict[str, dict[str, float]]:
    return SPECIES_ENV_THRESHOLDS.get(species_code or "pig", SPECIES_ENV_THRESHOLDS["pig"])


# ===== F08 气候风险规则（02-数据库 5.1 species.climate_risk 种子；PRD 3.4）=====
# 阈值口径与喂食的 weather_adjust（32/30℃）是两套数，不可混用（F08 RK-3）。
# 比较符刻意不一致：温度/降水用严格 > < ，风力用 >=（与 mock matchClimateRisks 逐值一致）。

CLIMATE_RISK_SEED: dict[str, dict[str, dict[str, float]] | None] = {
    "pig": {"heat_stress": {"temp_gt": 35}, "rainstorm": {"precip_mm_gt": 50}, "frost": {"temp_lt": 2}, "gale": {"wind_level_gte": 6}},
    "sheep": {"heat_stress": {"temp_gt": 30}, "rainstorm": {"precip_mm_gt": 40}, "frost": {"temp_lt": 0}, "gale": {"wind_level_gte": 6}},
    "cattle": {"heat_stress": {"temp_gt": 30}, "rainstorm": {"precip_mm_gt": 50}, "frost": {"temp_lt": 0}, "gale": {"wind_level_gte": 7}},
}

# 风险文案（label / impact / advice 与 mock RISK_COPY 逐字一致，冒烟按字面断言）
RISK_COPY: dict[str, dict[str, Any]] = {
    "heat_stress": {
        "label": "高温",
        "impact": "采食量下降、热应激风险上升，重则死淘率增加",
        "advice": ["减精料增饮水，调整喂食时段至早晚凉爽时", "加强圈舍通风与遮阳", "避免高温时段转群与运输"],
    },
    "rainstorm": {
        "label": "暴雨",
        "impact": "圈舍潮湿、垫料霉变与疫病传播风险上升",
        "advice": ["检修排水与屋面防漏", "饲料离地存放防潮防霉", "雨后及时更换垫料并加强消毒"],
    },
    "frost": {
        "label": "霜冻",
        "impact": "仔猪感冒腹泻风险上升，幼畜耐受差",
        "advice": ["检查供暖设备与门窗密闭", "增加垫料保温", "温水拌料，减少冷应激"],
    },
    "gale": {
        "label": "大风",
        "impact": "栏舍棚顶受损、电路中断风险",
        "advice": ["加固栏舍与棚顶覆盖物", "检查电路与备用电源", "收拢场内易坠易移物品"],
    },
}

# 官方预警类型 → 风险类型（首个正则命中为准）；等级 → 风险级
WARNING_TYPE_TO_RISK: list[tuple[str, str]] = [
    ("高温", "heat_stress"),
    ("暴雨|降雨|降水", "rainstorm"),
    ("寒潮|霜冻|低温", "frost"),
    ("大风|台风", "gale"),
]
WARNING_LEVEL_TO_RISK = {"blue": "low", "yellow": "mid", "orange": "high", "red": "high"}


def climate_risk(species_code: str | None) -> dict[str, dict[str, float]] | None:
    """未知物种 → None（EX-W7：匹配零命中，不报错）。"""
    return CLIMATE_RISK_SEED.get(species_code or "")


# ===== F07 物种生命周期参数（species-data.ts LIFECYCLE_PARAMS 原值）=====

LIFECYCLE_PARAMS: dict[str, dict] = {
    "pig": {
        "gestation_days": 114, "lactation_days": 28, "market_weight_kg": 120,
        "estrus_cycle_days": 21, "castration_age": "7~15日龄", "pregnancy_late_after_days": 91,
        "vaccination_program": [
            {"vaccine": "猪瘟兔化弱毒苗", "timing_day": 30, "target": "仔猪", "anchor": "birth", "note": "30 日龄首免"},
            {"vaccine": "口蹄疫O型灭活苗", "timing_day": 55, "target": "仔猪", "anchor": "birth", "note": "55 日龄二免"},
            {"vaccine": "大肠杆菌K88/K99", "timing_day": -21, "target": "妊娠母猪", "anchor": "due_date", "note": "产前 21 天免疫（母源抗体）"},
        ],
    },
    "sheep": {
        "gestation_days": 150, "lactation_days": 60, "market_weight_kg": 45,
        "estrus_cycle_days": 17, "castration_age": "2周~2月龄", "pregnancy_late_after_days": 91,
        "vaccination_program": [
            {"vaccine": "羔羊痢疾苗", "timing_day": -25, "target": "妊娠母羊", "anchor": "due_date", "note": "产前 20~30 天免疫（附录 D 关键管理提醒）"},
        ],
    },
    "cattle": {
        "gestation_days": 280, "lactation_days": 180, "market_weight_kg": 500,
        "estrus_cycle_days": 21, "castration_age": "育肥进场时", "pregnancy_late_after_days": 180,
        "vaccination_program": [],
    },
}


def lifecycle_params(species_code: str | None) -> dict:
    return LIFECYCLE_PARAMS.get(species_code or "pig", LIFECYCLE_PARAMS["pig"])


BREED_STATUS_LABEL = {"open": "空怀", "bred": "已配种", "pregnant": "妊娠", "nursing": "哺乳", "culled": "已淘汰"}
BATCH_STAGES = {"nursing", "weaning", "growing", "fattening"}

# 生命周期事件 → 允许的前置状态（F07 状态机；40003 拦截非法跳变）
EVENT_ALLOWED_FROM: dict[str, set[str] | None] = {
    "breeding": {"open"},
    "pregnancy_check": {"bred"},
    "birth": {"pregnant"},
    "weaning": {"nursing"},
    "abortion": {"bred", "pregnant"},
    "cull": {"open", "bred", "pregnant", "nursing"},
    "castration": None,  # 批次事件不校验母畜状态
    "dehorning": None,
    "stage_change": None,
}
EVENT_LABEL = {
    "breeding": "配种", "pregnancy_check": "妊娠检查", "birth": "分娩", "weaning": "断奶",
    "abortion": "流产", "cull": "淘汰", "castration": "阉割", "dehorning": "去角", "stage_change": "转阶段",
    "slaughter": "出栏",
}
INDIVIDUAL_EVENTS = {"breeding", "pregnancy_check", "birth", "weaning", "abortion", "cull"}

# ===== F03 引擎参数（mock 同值）=====
OFFLINE_AFTER_MS = settings.offline_after_seconds * 1000  # 07 协议真实口径 5 分钟；用 OFFLINE_AFTER_SECONDS 环境变量加速（演示默认 90s）
DEBOUNCE_COUNT = 3            # 连续超标 3 次才告警（FR-04）
DEBOUNCE_TTL_MS = 10 * 60 * 1000
MERGE_WINDOW_MS = 60 * 60 * 1000  # 同圈同类告警 1h 合并窗口（EX-C7）
DEDUP_TTL_MS = 2 * 60 * 60 * 1000  # msg_id 去重 TTL 2h（EX-C2）
HISTORY_MAX_POINTS = 120
HISTORY_SCAN_CAP = 20_000  # 曲线单次最多扫多少行；超上限只取最近这段并回 truncated 标记

# ===== 兽药库种子（knowledge-data.ts MEDICINE_LIB_SEED 原值；公开兽药标签用法示例整理）=====
MEDICINE_LIB_SEED: list[dict] = [
    {"species_code": "pig", "disease_name": "呼吸道疾病", "med_name": "阿莫西林可溶性粉", "med_code": "AMX-WS", "usage": "混饮，每 1L 水 60mg，连用 3~5 日（标签用法示例）", "withdrawal_period": 7, "is_forbidden": False, "contraindication": "对青霉素过敏动物禁用", "risk_level": None},
    {"species_code": "pig", "disease_name": "呼吸道疾病", "med_name": "氟苯尼考注射液", "med_code": "FFC-INJ", "usage": "肌注，一次量每 kg 体重 20mg，每 48 小时一次，连用 2 次（标签用法示例）", "withdrawal_period": 28, "is_forbidden": False, "contraindication": "繁殖期母畜慎用", "risk_level": None},
    {"species_code": "pig", "disease_name": "呼吸道疾病", "med_name": "延胡索酸泰妙菌素预混剂", "med_code": "TIA-PRE", "usage": "混饲，每 1000kg 饲料 100g，连用 5~7 日（标签用法示例）", "withdrawal_period": 3, "is_forbidden": False, "contraindication": "禁与莫能菌素、盐霉素联用", "risk_level": None},
    {"species_code": "pig", "disease_name": "呼吸道疾病", "med_name": "盐酸多西环素可溶性粉", "med_code": "DOX-WS", "usage": "混饮，每 1L 水 25mg，连用 3~5 日（标签用法示例）", "withdrawal_period": 28, "is_forbidden": False, "contraindication": "长期使用易致二重感染", "risk_level": None},
    {"species_code": "pig", "disease_name": "呼吸道疾病", "med_name": "头孢噻呋钠注射液", "med_code": "CEF-INJ", "usage": "肌注，一次量每 kg 体重 3~5mg，每日一次，连用 3 日（标签用法示例）", "withdrawal_period": 1, "is_forbidden": False, "contraindication": "对头孢菌素过敏动物禁用", "risk_level": None},
    {"species_code": "pig", "disease_name": "消化道疾病", "med_name": "硫酸黏菌素预混剂", "med_code": "COL-PRE", "usage": "混饲，每 1000kg 饲料 40g，连用 5 日（标签用法示例）", "withdrawal_period": 7, "is_forbidden": False, "contraindication": "肾功能不全动物慎用", "risk_level": None},
    {"species_code": "pig", "disease_name": "消化道疾病", "med_name": "乙酰甲喹注射液", "med_code": "MEQ-INJ", "usage": "肌注，一次量每 kg 体重 2.5mg，每日 2 次，连用 3 日（标签用法示例）", "withdrawal_period": 30, "is_forbidden": False, "contraindication": "超量易致中毒，严格按标签", "risk_level": None},
    {"species_code": "pig", "disease_name": "全身感染", "med_name": "磺胺间甲氧嘧啶钠注射液", "med_code": "SMM-INJ", "usage": "肌注，一次量每 kg 体重 50mg，每日 1~2 次，连用 3~5 日（标签用法示例）", "withdrawal_period": 28, "is_forbidden": False, "contraindication": "充足饮水防结晶尿，首次量加倍", "risk_level": None},
    {"species_code": "pig", "disease_name": "全身感染", "med_name": "恩诺沙星注射液", "med_code": "ENR-INJ", "usage": "肌注，一次量每 kg 体重 2.5mg，每日 1~2 次，连用 3~5 日（标签用法示例）", "withdrawal_period": 10, "is_forbidden": False, "contraindication": "幼龄动物慎用（软骨影响）", "risk_level": None},
    {"species_code": "pig", "disease_name": "寄生虫病", "med_name": "伊维菌素注射液", "med_code": "IVM-INJ", "usage": "皮下注射，一次量每 kg 体重 0.3mg（标签用法示例）", "withdrawal_period": 28, "is_forbidden": False, "contraindication": "柯利犬等敏感动物禁用；孕畜慎用", "risk_level": None},
    {"species_code": "pig", "disease_name": "寄生虫病", "med_name": "阿苯达唑片", "med_code": "ALB-TAB", "usage": "内服，一次量每 kg 体重 5~10mg（标签用法示例）", "withdrawal_period": 14, "is_forbidden": False, "contraindication": "孕畜禁用", "risk_level": None},
    {"species_code": "pig", "disease_name": "细菌感染", "med_name": "青霉素钾注射液", "med_code": "PEN-INJ", "usage": "肌注，一次量每 kg 体重 2~3 万单位，每日 2 次（标签用法示例）", "withdrawal_period": 1, "is_forbidden": False, "contraindication": "对青霉素过敏动物禁用，现配现用", "risk_level": None},
    {"species_code": "pig", "disease_name": "产科用药", "med_name": "葡萄糖酸钙注射液", "med_code": "CA-INJ", "usage": "静注，一次量 50~150ml，缓慢注射（标签用法示例）", "withdrawal_period": 0, "is_forbidden": False, "contraindication": "静注过快易致心律失常", "risk_level": None},
    {"species_code": "sheep", "disease_name": "呼吸道疾病", "med_name": "延胡索酸泰妙菌素预混剂（羊）", "med_code": "TIA-PRE-S", "usage": "混饲，每 1000kg 饲料 100g，连用 5~7 日（标签用法示例）", "withdrawal_period": 3, "is_forbidden": False, "contraindication": "禁与莫能菌素、盐霉素联用", "risk_level": None},
    {"species_code": "sheep", "disease_name": "呼吸道疾病", "med_name": "氟苯尼考注射液（羊）", "med_code": "FFC-INJ-S", "usage": "肌注，一次量每 kg 体重 20mg，每 48 小时一次，连用 2 次（标签用法示例）", "withdrawal_period": 28, "is_forbidden": False, "contraindication": "繁殖期母畜慎用", "risk_level": None},
    {"species_code": "sheep", "disease_name": "寄生虫病", "med_name": "伊维菌素注射液（羊）", "med_code": "IVM-INJ-S", "usage": "皮下注射，一次量每 kg 体重 0.2mg（标签用法示例）", "withdrawal_period": 28, "is_forbidden": False, "contraindication": "孕畜慎用", "risk_level": None},
    {"species_code": "sheep", "disease_name": "全身感染", "med_name": "磺胺间甲氧嘧啶钠注射液（羊）", "med_code": "SMM-INJ-S", "usage": "肌注，一次量每 kg 体重 50mg，每日 1~2 次，连用 3~5 日（标签用法示例）", "withdrawal_period": 28, "is_forbidden": False, "contraindication": "充足饮水防结晶尿", "risk_level": None},
    {"species_code": "sheep", "disease_name": "寄生虫病", "med_name": "阿苯达唑片（羊）", "med_code": "ALB-TAB-S", "usage": "内服，一次量每 kg 体重 10~15mg（标签用法示例）", "withdrawal_period": 14, "is_forbidden": False, "contraindication": "孕畜禁用", "risk_level": None},
    {"species_code": "sheep", "disease_name": "细菌感染", "med_name": "青霉素钾注射液（羊）", "med_code": "PEN-INJ-S", "usage": "肌注，一次量每 kg 体重 2~3 万单位，每日 2 次（标签用法示例）", "withdrawal_period": 1, "is_forbidden": False, "contraindication": "对青霉素过敏动物禁用，现配现用", "risk_level": None},
    {"species_code": "cattle", "disease_name": "呼吸道疾病", "med_name": "头孢噻呋钠注射液（牛）", "med_code": "CEF-INJ-C", "usage": "肌注，一次量每 kg 体重 2.2mg，每日一次，连用 3~5 日（标签用法示例）", "withdrawal_period": 1, "is_forbidden": False, "contraindication": "对头孢菌素过敏动物禁用", "risk_level": None},
    {"species_code": "cattle", "disease_name": "呼吸道疾病", "med_name": "氟苯尼考注射液（牛）", "med_code": "FFC-INJ-C", "usage": "肌注，一次量每 kg 体重 20mg，每 48 小时一次，连用 2 次（标签用法示例）", "withdrawal_period": 28, "is_forbidden": False, "contraindication": "繁殖期母畜慎用", "risk_level": None},
    {"species_code": "cattle", "disease_name": "寄生虫病", "med_name": "阿苯达唑片（牛）", "med_code": "ALB-TAB-C", "usage": "内服，一次量每 kg 体重 10~15mg（标签用法示例）", "withdrawal_period": 14, "is_forbidden": False, "contraindication": "孕畜禁用", "risk_level": None},
    {"species_code": "cattle", "disease_name": "全身感染", "med_name": "恩诺沙星注射液（牛）", "med_code": "ENR-INJ-C", "usage": "肌注，一次量每 kg 体重 2.5mg，每日 1~2 次，连用 3~5 日（标签用法示例）", "withdrawal_period": 10, "is_forbidden": False, "contraindication": "幼龄动物慎用（软骨影响）", "risk_level": None},
    {"species_code": "pig", "disease_name": "促生长（违禁）", "med_name": "盐酸克伦特罗（瘦肉精）", "med_code": "CLB-BAN", "usage": "食品动物禁用", "withdrawal_period": 0, "is_forbidden": True, "contraindication": "食品动物中禁止使用（农业农村部公告第 250 号）", "risk_level": "high"},
    {"species_code": "pig", "disease_name": "促生长（违禁）", "med_name": "沙丁胺醇", "med_code": "SAL-BAN", "usage": "食品动物禁用", "withdrawal_period": 0, "is_forbidden": True, "contraindication": "食品动物中禁止使用（农业农村部公告第 250 号）", "risk_level": "high"},
    {"species_code": "pig", "disease_name": "抗感染（违禁）", "med_name": "氯霉素", "med_code": "CAP-BAN", "usage": "食品动物禁用", "withdrawal_period": 0, "is_forbidden": True, "contraindication": "食品动物中禁止使用（农业农村部公告第 250 号）", "risk_level": "high"},
    {"species_code": "pig", "disease_name": "抗感染（违禁）", "med_name": "呋喃唑酮", "med_code": "FZD-BAN", "usage": "食品动物禁用", "withdrawal_period": 0, "is_forbidden": True, "contraindication": "食品动物中禁止使用（农业农村部公告第 250 号）", "risk_level": "high"},
]
