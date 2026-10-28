# 云上牧场 · 智慧养殖多智能体平台

面向中小养殖户的智慧养殖平台：环境风险主动告警、AI 疫病问诊（带 HITL 兽医复核）、
最小成本饲料配方、全生命周期台账与休药期合规。

- 后端：FastAPI + SQLAlchemy(async) + PostgreSQL + LangGraph + Milvus
- 前端：Vue 3.5 + Vite 7 + Vue Router + TanStack Query (Vue) + Pinia + Tailwind v4 + Three.js
- 设备接入：EMQX(MQTT) → 后端桥接，或设备 `X-Device-Key` 直连 `/api/device/ingest`

---

## 目录结构

```
智慧养殖多智能体平台/
├── backend/                 # FastAPI 服务（业务 + Agent 工作流 + 设备接入）
│   ├── app/api/             # 18 个路由模块
│   ├── app/agents/          # LangGraph 13 节点工作流 / 知识库 / 工具
│   ├── app/services/        # 配方 LP / 告警 / WS / 气象 / MQTT 桥接 / LLM
│   ├── app/core/            # 配置 / DB / 鉴权 / 响应契约 / 限流
│   ├── smoke_*.py           # 6 组冒烟脚本（396 项断言）
│   └── run.py               # 启动入口（Windows 必须用它，见下）
├── smart-farming-vue/       # Web 前端（Vue 3，唯一的业务前端）
├── smart-farming-project/   # 旧版 React 前端（已停用，留作对照，勿再启动）
├── ppt-frontend/            # 答辩 PPT 工程（与平台运行无关，可忽略）
├── deploy/                  # mediamtx 等部署配置
├── docs/                    # 01~26 设计与 PRD；08 = 遗留问题登记
├── _archive/                # 已下线实现的存档（mock 内核 / Alembic / 临时脚本）
├── docker-compose.yml       # PostgreSQL / Redis / EMQX / Milvus / mediamtx
└── .env.example             # 环境变量模板
```

---

## 快速启动

### 1. 基础设施

```bash
docker compose up -d          # postgres / redis / emqx / milvus / mediamtx
```

### 2. 后端

```bash
cd backend
pip install -r requirements.txt

# 复制环境变量模板并填入 Key（LLM / 高德气象）
cp ../.env.example ../.env

python run.py                 # 监听 127.0.0.1:8000
```

> **Windows 必须用 `python run.py`**：psycopg 的异步模式要求 `SelectorEventLoop`，
> 而 `python -m uvicorn` 在 Windows 上起的是 `ProactorEventLoop`，LangGraph 的
> checkpoint 连接池会 30s 超时。`run.py` 在事件循环创建前把策略换掉。

健康检查：`GET http://127.0.0.1:8000/api/health`

### 3. 前端

```bash
cd smart-farming-vue
npm install
npm run dev                   # http://localhost:3015
```

前端默认连 `http://<当前主机>:8000/api`（dev server 3015 端口时自动推断）；
打包产物走相对路径 `/api`，由 nginx 或同域后端托管。需要指向别处时在
`smart-farming-vue/.env` 里设 `VITE_REAL_API_BASE`。

### 4. 一键启动（Windows）

双击根目录 `启动项目.bat`：起 docker → 起后端 → 起前端 → 健康检查。

---

## 环境变量

见 `.env.example`。最小可用集合：

| 键 | 说明 |
|---|---|
| `LLM_API_KEY` | DashScope（百炼）Key，问诊/识图/意图分类共用 |
| `AMAP_WEATHER_KEY` | 高德天气 Key（缺省时气象走降级分支） |
| `ADMIN_SEED_PHONE` / `ADMIN_SEED_PASSWORD` | 管理员种子账号（缺省则不建号） |
| `MQTT_ENABLED` | `true` 才启动 EMQX 订阅桥接 |
| `CORS_ORIGINS` | 前端来源白名单，逗号分隔 |

敏感值只存在服务端进程内存，不下发前端 bundle、不写日志。

---

## 验证

```bash
cd backend
python smoke_backend.py     # 账户/场舍/存栏/台账（89 项）
python smoke_agent.py       # 问诊链路 + HITL（91 项）
python smoke_feeding.py     # 喂食中心 + LP 配方（87 项）
python smoke_contract.py    # 契约一致性（71 项）
python smoke_vision_kb.py   # 视觉事件 + 知识库（41 项）
python smoke_ws.py          # WebSocket 告警通道（17 项）
```

前端：`npm run typecheck` + `npm run build`。

---

## 已知限制

见 [`docs/08-遗留问题与风险登记.md`](docs/08-遗留问题与风险登记.md)。摘要：

- 去重/防抖/合并窗口/WS 连接/LLM 限流的状态在**进程内**，多 worker 部署会各算各的（C-26）
- EMQX 的 ACL 尚未版本化，broker 侧只做了默认认证（C-27）
- 视觉告警目前只有演示注入，没有真实边缘推理服务在推（C-28）
- 强制出栏缺审批流，靠备注门槛 + 台账留痕缓解（C-25）
