"""只读配置 — 物种/品种列表（species-data 静态种子的服务端等价）。"""

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.response import ok
from app.core.security import get_current_user

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])

SPECIES_LIST = [
    {"species_code": "pig", "species_name": "猪", "category": "畜禽"},
    {"species_code": "cattle", "species_name": "牛", "category": "畜禽"},
    {"species_code": "sheep", "species_name": "羊", "category": "畜禽"},
]

VARIETY_MAP: dict[str, list[dict[str, str]]] = {
    "pig": [
        {"variety_code": "pig_fattening", "variety_name": "育肥猪"},
        {"variety_code": "pig_sow", "variety_name": "母猪"},
        {"variety_code": "pig_piglet", "variety_name": "仔猪"},
        {"variety_code": "common", "variety_name": "通用"},
    ],
    "cattle": [
        # 前两位有专项营养标准；其余新品种无模板时由 feed_engine 按参考品种回落
        {"variety_code": "cattle_beef", "variety_name": "肉牛"},
        {"variety_code": "cattle_dairy", "variety_name": "奶牛"},
        {"variety_code": "cattle_simmental", "variety_name": "西门塔尔牛"},
        {"variety_code": "cattle_angus", "variety_name": "安格斯牛"},
        {"variety_code": "cattle_charolais", "variety_name": "夏洛莱牛"},
        {"variety_code": "cattle_luxi_yellow", "variety_name": "鲁西黄牛"},
        {"variety_code": "cattle_qinchuan", "variety_name": "秦川牛"},
        {"variety_code": "common", "variety_name": "通用"},
    ],
    "sheep": [
        # 前两位有专项营养标准；其余新品种无模板时由 feed_engine 按参考品种回落
        {"variety_code": "sheep_hu", "variety_name": "湖羊"},
        {"variety_code": "sheep_small_tailed_han", "variety_name": "小尾寒羊"},
        {"variety_code": "sheep_dorper", "variety_name": "杜泊羊"},
        {"variety_code": "sheep_suffolk", "variety_name": "萨福克羊"},
        {"variety_code": "sheep_tan", "variety_name": "滩羊"},
        {"variety_code": "sheep_wool", "variety_name": "绵羊（本地土种）"},
        {"variety_code": "sheep_boer", "variety_name": "波尔山羊"},
        {"variety_code": "common", "variety_name": "通用"},
    ],
}


@router.get("/species/list")
async def species_list() -> dict[str, Any]:
    return ok({"items": SPECIES_LIST})


@router.get("/variety/list")
async def variety_list(species_code: str = Query(default="pig")) -> dict[str, Any]:
    return ok({"items": VARIETY_MAP.get(species_code, [{"variety_code": "common", "variety_name": "通用"}])})
