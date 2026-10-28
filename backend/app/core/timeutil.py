"""业务自然日口径 —— 统一按东八区，禁止用服务器本地时区或 UTC 直接取「今天」。

`datetime.now(timezone.utc).date()` 在 00:00–08:00（CST）仍返回昨天：喂养打卡、
休药期起算、出栏日期会整体错一天；`date.today()` 又依赖部署机时区，换台机器就变。
"""

from datetime import date, datetime, timedelta, timezone

CST = timezone(timedelta(hours=8), "CST")


def today_cst() -> date:
    return datetime.now(CST).date()
