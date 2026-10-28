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
        {"variety_code": "cattle_beef", "variety_name": "肉牛"},
        {"variety_code": "cattle_dairy", "variety_name": "奶牛"},
        {"variety_code": "common", "variety_name": "通用"},
    ],
    "sheep": [
        {"variety_code": "sheep_hu", "variety_name": "湖羊"},
        {"variety_code": "sheep_small_tailed_han", "variety_name": "小尾寒羊"},
        {"variety_code": "common", "variety_name": "通用"},
    ],
}


@router.get("/species/list")
async def species_list() -> dict[str, Any]:
    return ok({"items": SPECIES_LIST})


@router.get("/variety/list")
async def variety_list(species_code: str = Query(default="pig")) -> dict[str, Any]:
    return ok({"items": VARIETY_MAP.get(species_code, [{"variety_code": "common", "variety_name": "通用"}])})
