# 智慧养殖多智能体平台 — API 接口详细设计文档

- 文档版本：V1.8
- 依据文档：PRD V1.7 第五章接口清单，逐条展开
- V1.8 变更（2026-10-17 F14 个体绑定收口）：① **9.1 请求体补 `individual_id`**（可选，开流前做归属校验：不存在 40401 / 越权 40302 / 与 `pen_id` 不同圈 42201），绑定后圈舍以该个体所在圈为准，不再走"先答后问"；② 9.1 `done` 事件与 9.3 消息 `meta` 补 `individual{individual_id,ear_tag_no,pen_name}`，供历史回看复原绑定与"记入个体档案"预选；③ disease 台账 `record_ext` 绑定轮写 `tag_no`/`individual_id`，并按实测把 **AI 问诊/用药与兽医复核的全部动态列键登记进 `EXT_FIELD_LABELS`**（`disease_name`/`risk_level`/`diagnosis_mode`/`llm_model`/`vision_signs`/`intent`/`source`/`session_id`/`thread_id`/`med_suggest`/`max_withdrawal_days`/`withdrawal_period`/`withdrawal_until`/`review_*`/`revised_*`/`vet_user_id`/`tag_no`/`individual_id`，10.1——未登记的键按兜底规则裸显英文列头）；④ 高危 `vet_review.original_diagnosis` 前缀耳标号（复核单无个体列，排队时得知道是哪一头）
- V1.7 变更（2026-10-16 端到端审计收口）：① **8.2 请求体字段名由 `handled_note` 正名为 `note`**（后端一直读 `note`，本文档写错导致前端照错实现，切真后「标记已处理」恒 42201；响应侧仍为 `handled_note`）；② 8.1 补 `farm_id`/`from`，并去掉 `/alert/list` 的 `type` alias（与本文档 8.1 口径对齐）；③ 新增 6.10 `GET /feed/standards`、7.4 `POST /device/{id}/rotate-secret`，7.1 响应补一次性 `device_secret`；④ **`farm_id` 统一补齐**：`/record/list`、`/record/export`、`/vision/events`、`/feeding/overview` 此前不收，前端一直在发 → 筛选静默失效、导出恒含全部场、越权读他场；⑤ `/record/list` 的 `ext_columns` 契约明确为 `[{field,label}]`（后端曾返回裸字符串数组，前端表头渲染成空白）；⑥ `/api/ai/*` 全部改为需登录（此前是免鉴权的 qwen 文本/视觉代理）；⑦ `POST /api/scene/analyze-photo` 实装，`/scene` 入切真清单，前端"浏览器自配 sf_llm_base"特例删除（见 08 C-11）；⑧ 删除 `kb.py` 里被 `admin.py` 同名路由遮蔽、永不可达的 `/kb/list` 副本；⑨ `/kb/upload` 超长正文由静默截断改为拒收（与 `/admin/kb` 同口径）
- V1.6 变更（F09 实施回写，承接《17-功能PRD-F09》文档同步项①）：7.3 展开视觉事件列表契约——补筛选参数（pen_id 归属校验 40302/from&to 范围/page 分页）、响应字段（event_label 中文标签、level 应用层计算、device_sn 扩展）、collect_time 倒序与 bbox 不返回口径
- V1.5 变更（F11 实施回写，承接《20-功能PRD-F11》文档同步项①②）：9.1 补 data_query 分支契约（只读工具白名单四把、done 事件 denied/clarify 扩展、写指令拒绝）、圈舍定位规则补两级匹配与数据查询子图适用、消息长度 500 字截断（EX-D11 问诊/查询统一）
- V1.4 变更（F07 实施回写，承接《15-功能PRD-F07》文档同步项①③④⑤）：① 5.4 请求体补「male_count/female_count 语义为出栏数量（正整数）」注释（C-05 落地）；③ 5.2 补 birth 事件 detail 公母拆分结构 {live_male, live_female, dead_count} 与 castration/dehorning/stage_change detail 说明；④ 五节新增 5.6 GET /medicine/usage 与 5.7 GET /medicine/list（F07 出栏页具体化接口）；⑤ 5.3.1 引用「06 规范 3.8」正名为「4.8 告警中心页」
- V1.3 变更（F06 实施回写，承接《14-功能PRD-F06》文档同步项①③④）：6.2 补请求体 pen_id 与"同 pen+date+feed_type 覆盖更新"口径；6.4 补请求体契约 {pen_id, constraints?} 与响应 options[].formula；6.7 补 vs_standard.level 枚举（normal/low，low=ratio<0.8）；6.1 响应补 plan_source/active_formula 扩展字段
- V1.2 变更（评审修复）：chat 请求补可选 pen_id 与圈舍定位规则；lifecycle 事件表定稿分娩/断奶存栏口径；feed-plan 补计算优先级；vet/review 驳回修改需过兽药库校验；device 请求补 device_sn
- V1.1 变更：新增转群（4.4）、提醒处理（5.3.1）、看板聚合（十四节）；40004/40005 错误码口径澄清（confirm_type）；feed/weight 确认场景细化；device.source 说明修正
- 基础地址：`http://<host>:8000/api`

---

## 一、通用约定

### 1.1 鉴权

- 除 `注册/登录` 外全部接口要求 Header：`Authorization: Bearer <JWT>`
- JWT Payload：`{"user_id":1,"role":"farmer","exp":...}`，有效期 24h
- 多租户：服务端从 JWT 取 user_id，所有查询强制归属过滤，**客户端传来的 user_id 一律忽略**；角色例外（vet 仅待复核病例、admin 仅全局配置库）见 01 架构文档第八节

### 1.2 统一响应结构

```json
// 成功
{"code": 0, "message": "ok", "data": { ... }}
// 失败
{"code": 40001, "message": "休药期未满，禁止出栏", "data": {"med_name":"氟苯尼考","withdrawal_left_days":5}}
```

### 1.3 错误码表

| code | 含义 | 典型场景 |
|------|------|----------|
| 0 | 成功 | - |
| 40001 | 休药期未满拦截 | 出栏登记（EX-11） |
| 40002 | 存栏变动导致负数 | 存栏校验（EX-10） |
| 40003 | 母畜状态非法跳变 | 生命周期事件（EX-12） |
| 40004 | 配方营养指标不达标 | 预留强拦截场景；V1.0 常规路径在 6.3 响应 data.nutrition_check 内标注（EX-16） |
| 40005 | 录入值异常需确认 | data.confirm_type 区分：feed_over（EX-14 投喂超 3 倍）/ weight_drop（EX-15 称重骤降），重发附 confirm=true |
| 40101 | 未登录 / token 失效 | 401 |
| 40301 | 无权限（角色不符） | 兽医接口被 farmer 访问 |
| 40302 | 数据归属校验失败 | 访问他人养殖场 |
| 40401 | 资源不存在 | - |
| 42201 | 参数校验失败 | data 返回字段级错误 |
| 50001 | LLM 调用失败 | 问诊（EX-03，前端提示可重试） |
| 50002 | 知识库无该病症资料 | RAG 无结果（EX-04） |
| 50003 | 气象服务暂不可用 | 无缓存降级失败（EX-02） |

### 1.4 分页约定

`GET` 列表接口统一：`?page=1&page_size=20`，响应：

```json
{"code":0,"message":"ok","data":{"total":123,"page":1,"page_size":20,"items":[...]}}
```

---

## 二、认证与账户

### 2.1 POST /auth/register

```json
// 请求
{"phone": "13800000000", "password": "******", "nickname": "文彬养殖场"}
// 响应 data
{"user_id": 1, "phone": "13800000000", "first_login": true}
```
- 手机号格式本地校验（V1.0 无短信验证码）；密码 8~32 位；bcrypt 存储

### 2.2 POST /auth/login

```json
// 请求
{"phone": "13800000000", "password": "******"}
// 响应 data
{"token": "eyJ...", "expires_in": 86400, "user": {"user_id":1,"nickname":"...","role":"farmer","main_species_code":null,"guide_done":false}}
```
- `guide_done`：是否完成首登引导（有养殖场即视为完成），前端据此跳引导向导

### 2.3 PUT /user/main-species

```json
{"main_species_code": "pig"}
```
- 语义：历史数据保留，仅影响新建圈舍默认物种（EX-08）

### 2.4 PUT /user/password

```json
{"old_password": "...", "new_password": "..."}
```

---

## 三、物种与品种

### 3.1 GET /species/list

```json
{"code":0,"data":{"items":[
  {"species_code":"pig","species_name":"猪","category":"畜禽"},
  {"species_code":"cattle","species_name":"牛","category":"畜禽"},
  {"species_code":"sheep","species_name":"羊","category":"畜禽"}
]}}
```

### 3.2 GET /variety/list?species_code=pig

```json
{"code":0,"data":{"items":[
  {"variety_code":"pig_fattening","variety_name":"育肥猪"},
  {"variety_code":"pig_sow","variety_name":"母猪"},
  {"variety_code":"pig_piglet","variety_name":"仔猪"},
  {"variety_code":"common","variety_name":"通用"}
]}}
```

---

## 四、养殖场与圈舍

### 4.1 POST /farm（PUT/GET/DELETE 同路径）

```json
// 请求
{"farm_name":"示例家庭农场","province":"湖南省","city":"长沙市","county":"长沙县",
 "adcode":"430121","address":"XX镇XX村","contact_person":"张某某","phone":"13800000000"}
```
- adcode 由前端行政区划选择器自动带出；DELETE 为逻辑删除

### 4.2 POST /pen

```json
{"farm_id":1,"pen_name":"1号育肥圈","species_code":"pig","variety_code":"pig_fattening",
 "male_count":10,"female_count":40,"batch_stage":"fattening"}
```
- species_code 继承主物种，V1.0 前端不可改
- 响应含 `pen_id`

### 4.3 POST /pen/{pen_id}/stock-change

```json
// 请求
{"change_type":"birth","male_delta":6,"female_delta":5,"remark":"3号母猪产仔11头"}
// 响应 data
{"change_id":9,"male_count":16,"female_count":45}
```
- 服务端事务：写 stock_change → 更新 pen 公母数 → 落台账 stock_change 类型；负数返回 40002

### 4.4 POST /pen/{pen_id}/transfer（转群，V1.1 新增）

```json
// 请求：把本圈指定数量牲畜转入目标圈（断奶仔猪转保育圈、育肥转群等）
{"target_pen_id":2,"male_count":6,"female_count":5,"remark":"断奶转保育"}
// 响应 data：两笔联动
{"out_change_id":16,"in_change_id":17,
 "source_pen":{"male_count":10,"female_count":40},
 "target_pen":{"male_count":6,"female_count":5}}
```

- 服务端单事务：源圈 stock_change(transfer_out，负增量) + 目标圈 stock_change(transfer_in，正增量)，related_change_id 互指；各自落台账
- 源圈数量不足或导致存栏为负返回 40002；目标圈 species 应与源圈一致（V1.0 不允许混养）

---

## 五、生命周期管理

### 5.1 POST /breeding-female

```json
{"pen_id":1,"tag_no":"M-012","variety_code":"pig_sow","parity":2,"breed_status":"open"}
```

### 5.2 POST /lifecycle/event

```json
// 配种
{"event_type":"breeding","female_id":3,"event_date":"2026-10-09",
 "detail":{"semen_type":"人工授精"}}
// 响应 data（自动算预产期）
{"event_id":12,"female_status":"bred","due_date":"2027-01-31"}
```

```json
// 分娩
{"event_type":"birth","female_id":3,"event_date":"2027-01-31",
 "detail":{"live_count":10,"dead_count":1}}
// 副作用：母畜状态→nursing；pen 存栏 +live_count（birth 类型 stock_change）；落台账
```

| event_type | 状态副作用 | 其他副作用 |
|------------|-----------|------------|
| breeding | open→bred | 算 due_date |
| pregnancy_check | bred→pregnant | - |
| abortion | →open（胎次不变，清空 due_date/breed_date） | - |
| birth | pregnant→nursing | birth 存栏变动（**仔猪计入母猪所在圈**，V1.2 定稿） |
| weaning | nursing→open，parity+1 | **不隐式搬动仔猪存栏**；仔猪转保育/育肥圈必须显式调 4.4 transfer（V1.2 定稿） |
| castration / dehorning | 无（批次属性） | detail 标记 |
| stage_change | 批次 batch_stage 流转 | - |
| slaughter / cull | →culled（个体） | 对应存栏变动 |
- 非法跳变返回 40003（EX-12）
- **detail 字段结构（V1.4 补，F07 具体化）**：birth = {live_male, live_female, dead_count}（公母拆分是 birth 存栏变动必需；03-API 5.2 原示例 {live_count, dead_count} 为汇总口径，实现以公母拆分为准）；castration/dehorning = {method}（操作方式，选填）；stage_change = {to_stage}（目标阶段，须在 F02 FR-03 白名单内，sold 由出栏流程设置不在内）；breeding/pregnancy_check/abortion/weaning detail 为可选备注性质

### 5.3 GET /lifecycle/reminders?farm_id=1

```json
{"code":0,"data":{"items":[
  {"reminder_type":"due_date","title":"M-012 预产期还有 2 天","remind_date":"2026-10-11","pen_name":"母猪圈"},
  {"reminder_type":"vaccine","title":"妊娠后期母羊：羔羊痢疾苗（产前20-30天）","remind_date":"2026-10-15"}
]}}
```

### 5.3.1 POST /reminder/{reminder_id}/handle（提醒处理，V1.1 新增）

```json
{"action":"done","note":"已安排接产"}   // 标记完成；note 选填
```

- 预产期/断奶/免疫提醒在看板与告警中心均以 todo 型展示（06 规范 4.8），处理即置 status=done；expired 由每日定时任务置位

### 5.4 POST /pen/{pen_id}/slaughter（出栏登记，含休药期硬校验）

```json
// 请求（male_count/female_count 语义为出栏数量，正整数，不超过该圈对应性别存栏；C-05）
{"male_count":5,"female_count":10,"avg_weight_kg":115.5,"destination":"本地屠宰场"}
// 校验通过 响应 data
{"change_id":15,"withdrawal_check":"passed","record_id":201}
// 校验拦截（HTTP 200 + code 40001）
{"code":40001,"message":"休药期未满，禁止出栏",
 "data":{"med_name":"氟苯尼考注射液","usage_date":"2026-09-20","withdrawal_until":"2026-10-18","left_days":9}}
// 用户坚持出栏：重发请求附 {"force":true,"force_note":"已告知休药期要求，自行承担合规责任"} → 留痕落台账
```
- 数量超存栏返回 40002（与存栏变动同码）；force=true 时 force_note 必填（缺省 42201）
- 副作用（三表联动一个事务）：stock_change(sale) + lifecycle_event(slaughter) + breeding_record(record_type=slaughter，含休药期校验结果与 force 留痕)；全部出栏后 pen.batch_stage 置 sold

### 5.5 POST /medicine/usage

```json
{"pen_id":1,"med_id":8,"usage_date":"2026-10-09","dosage":"按说明书拌料","operator_note":"呼吸道预防"}
// 响应 data：withdrawal_until 由服务端算出
{"usage_id":4,"withdrawal_until":"2026-10-16"}
```
- med_id 必须在 medicine_lib 且 is_forbidden=false，否则 42201（违禁/库外药拦截，与问诊链路同底线）
- withdrawal_until = usage_date + medicine_lib.withdrawal_period，服务端计算写入（02-数据库口径，前端不传）

### 5.6 GET /medicine/usage?pen_id=&days=90（V1.4 新增，F07 具体化）

```json
{"code":0,"data":{"items":[
  {"usage_id":4,"med_id":8,"med_name":"氟苯尼考注射液","usage_date":"2026-10-09",
   "dosage":"按说明书拌料","withdrawal_until":"2026-11-06","withdrawal_period":28,
   "withdrawal_left_days":24,"operator_note":"呼吸道预防"}]}}
```
- 按圈舍查询近 N 天用药列表（默认 90 天），休药期校验与出栏页「查看近期用药」同源复用；withdrawal_left_days ≤0 表示已过休药期

### 5.7 GET /medicine/list（V1.4 新增，F07 具体化）

```json
{"code":0,"data":{"items":[
  {"med_id":8,"med_name":"氟苯尼考注射液","withdrawal_period":28,"disease_name":"猪喘气病继发感染"}]}}
```
- farmer 侧兽药库查询（出栏页用药登记下拉数据源）；**违禁药（is_forbidden=true）过滤不下发**，登记时仍双重校验 42201

---

## 六、喂食中心

### 6.1 GET /pen/{pen_id}/feed-plan

```json
{"code":0,"data":{"pen_id":1,"stage":"fattening","stock":50,
 "daily_plan_kg":100.0,"meals_per_day":2,"per_meal_kg":50.0,
 "basis":"依据 NY/T 育肥猪饲养标准 30-60kg 阶段",
 "plan_source":"standard",
 "active_formula":null}}
```

- **计算优先级（V1.2 定稿）**：①圈舍有生效配方 → Σ(配方原料 daily_kg_per_head × 当前存栏)，并纳入当日 weather_adjust 气象调整；②无生效配方 → 按 feed_nutrition_lib 阶段补饲标准兜底；两者都无 → 返回 null 并提示"请先生成配方"
- 能繁母猪圈不参与 batch_stage 配方匹配，按母畜品种（如 pig_sow）配置取计划
- **扩展字段（V1.3，F06 具体化）**：`plan_source` 取 formula/standard/none 标注计划来源（前端"生效配方/标准兜底"标注口径）；`active_formula` 携带 6.3 结构的当前生效配方完整对象（无则 null），供喂食中心配方 Tab 一次拉取

### 6.2 POST /feeding/record

```json
// 请求（V1.3 补 pen_id，按圈批量登记必须逐圈指定；F06 文档同步项①）
{"pen_id":1,"feed_date":"2026-10-09","feed_type":"concentrate","amount_kg":100.0,"as_planned":true}
// amount_kg 超参考量 3 倍 → code 40005（confirm_type=feed_over），前端弹确认，重发附 confirm=true
```
- 服务端登记时自动快照当日计划量写入 planned_kg（存栏/配方后续变化不影响历史对照）；as_planned=true 时 amount_kg 可省略，服务端按计划量填入
- **同 pen + feed_date + feed_type 重复登记按覆盖更新**（当日重复打卡不产生双记录，V1.3 定稿，F06 待确认②采纳）
- GET /feeding/record?pen_id=&from=&to= 返回 planned_kg vs amount_kg 历史对照

### 6.3 POST /feed/formula/generate

```json
// 请求（可带限制）
{"pen_id":1,"constraints":{"exclude_ingredients":["豆粕"],"reason":"缺货"}}
// 响应 data
{"formula_id":21,
 "ingredients":[{"name":"玉米","ratio_pct":62,"daily_kg_per_head":1.5},
                {"name":"麸皮","ratio_pct":18,"daily_kg_per_head":0.44},
                {"name":"菜籽粕","ratio_pct":15,"daily_kg_per_head":0.36},
                {"name":"预混料","ratio_pct":5,"daily_kg_per_head":0.12}],
 "feeds_per_day":2,"cost_per_head_day":2.31,
 "nutrition_check":{"粗蛋白":{"required":"14~16%","actual":"15.2%","pass":true}},
 "basis":"依据《湖南省生猪健康养殖技术规程》育肥期饲养要求",
 "disclaimer":"AI 生成，仅供参考"}
```
- 不达标项 pass=false 以 data.nutrition_check 标注（EX-16 前端红字提示；40004 仅预留强拦截场景）

### 6.4 POST /feed/formula/alternatives

```json
// 请求（V1.3 补请求体契约，F06 文档同步项③；与 6.3 一致）
{"pen_id":1,"constraints":{"exclude_ingredients":["豆粕"],"reason":"缺货"}}
// 响应 data：2~3 套方案 + 成本对比
{"options":[
  {"formula_id":22,"label":"方案一（菜籽粕替代·营养平衡）","cost_per_head_day":5.59,"nutrition_pass":true,
   "formula":{ "...": "6.3 结构的完整配方对象，供方案卡片展开详情与直接激活" }},
  {"formula_id":23,"label":"方案二（稻谷替代·成本最低）","cost_per_head_day":5.51,"nutrition_pass":false,"miss":["消化能"]},
  {"formula_id":24,"label":"方案三（鱼粉替代·营养更优）","cost_per_head_day":5.94,"nutrition_pass":false,"miss":["赖氨酸"]}
]}
```
- 每套方案同样落库为候选态（is_active=false），可凭 formula_id 直接调 6.5 激活；`formula` 为 V1.3 扩展（F06 具体化：方案卡片展开详情所需，避免二次请求）

### 6.5 POST /feed/formula/{formula_id}/activate

- 副作用：同圈舍旧配方 is_active=false；落台账 feed 类型记录"某圈舍换配方"

### 6.6 GET/POST /feed/price

```json
// POST 修改价
{"ingredient_name":"玉米","unit_price":2.75,"price_date":"2026-10-09"}
// GET 响应 items 附 price_age_days（EX-17：>30 天前端提示改价）
```

### 6.7 POST/GET /feeding/weight

```json
// POST
{"pen_id":1,"weigh_date":"2026-10-09","avg_weight_kg":62.5,"sample_count":5}
// 响应 data：自动算日增重
{"weight_id":7,"daily_gain_kg":0.6800,
 "vs_standard":{"standard":0.75,"ratio":0.907,"level":"normal"}}
// 较上次下降超 10% → code 40005（confirm_type=weight_drop），需 confirm（EX-15）
```
- **vs_standard.level 枚举（V1.3 补，F06 文档同步项④）**：`normal` / `low`（low = ratio < 0.80）；单次 low 仅响应标注不置顶提醒，连续 2 次 ratio<0.8 由前端展示 mid 提醒 + 归因建议（F06 PRD FR-08，切真后归因随告警服务层三联动统一）

### 6.8 GET /pen/{pen_id}/daily-gain

```json
{"code":0,"data":{"points":[{"date":"2026-09-20","avg_weight":52.0},
                             {"date":"2026-10-05","avg_weight":61.0},
                             {"date":"2026-10-09","avg_weight":62.5}],
 "daily_gain":[{"date":"2026-10-05","gain":0.53},{"date":"2026-10-09","gain":0.68}],
 "standard_line":0.75}}
```

### 6.9 GET /pen/{pen_id}/feed-conversion（出栏后）

```json
{"code":0,"data":{"total_feed_kg":12340.0,"total_gain_kg":4650.0,"fcr":2.65,
 "avg_cost_per_head_day":2.31}}
```

- 总增重口径：出栏总重 −（该批次**首次称重记录均重 × 出栏时存栏数**）；全程无称重记录的批次返回 `{"fcr":null,"reason":"缺少称重数据，无法计算料肉比"}`，禁止返回 0

---

### 6.10 GET /feed/standards?species_code=&variety_code=（V1.7 新增）

- 营养标准档位（`feed_nutrition_lib`），供喂食中心「分阶段饲喂方案」卡片渲染
- **为什么新增**：此前这张卡片是浏览器 filter 前端常量 `FEED_NUTRITION_LIB_SEED`，而配方计算读的是同名内容的库表（表里的种子还是从那个常量 `scripts/export_feed_seed.ts` 导出的）——同一份标准存两处，库里改了界面不跟。现在展示与计算同读一张表
- 响应字段与库表同构：`{feed_id, species_code, variety_code, stage, sex_type, sow_phase, min_weight, max_weight, nutrition{daily_feed_kg_per_head,feeds_per_day,indicators[]}, formula[{name,ratio_pct}], weather_adjust, source}`；`min/max_weight` 已转 float（Numeric 直接进 JSON 会序列化失败）

---

## 七、设备与数据

### 7.1 POST /device

```json
{"farm_id":1,"pen_id":1,"device_type":"sensor","device_name":"1号圈温湿度节点",
 "device_sn":"DEV001","mqtt_topic":"farm/1/pen/1/DEV001/data"}
```
- **device_sn 必填且全局唯一（V1.2）**：MQTT topic 中的 device_id 即此字段，消费者按 sn 回表校验归属；响应含 `device_id`
- **响应含一次性 `device_secret`（V1.7，C-24）**：设备走 HTTP 上行时用它做 `X-Device-Key`，库内只存 sha256，丢了用 7.4 轮换
- 模拟设备由设备管理页标记 `source:"sim"`（device 表独立字段，列表接口返回来源列）

### 7.4 POST /device/{device_id}/rotate-secret（V1.7，C-24）

- 重签发设备密钥，**旧值立即失效**；`{"device_id":12,"device_secret":"<一次性明文>"}`
- 越权设备 40302 / 不存在 40401；本接口只回传明文，不可再查

### 7.2 GET /sensor/history?pen_id=1&metric=temperature&from=2026-10-08&to=2026-10-09

```json
{"code":0,"data":{"metric":"temperature","points":[
  {"time":"2026-10-08T00:05:00","value":22.4}, ...]}}
```
- 大区间自动降采样（返回 ≤2000 点）

### 7.3 GET /vision/events?pen_id=&farm_id=&from=&to=&page=&page_size=（V1.2 展开，F09 同步项①；V1.7 补 farm_id）

- **筛选**：`pen_id`（归属校验，越权 40302；缺省=名下全部圈聚合）/ `from` & `to`（collect_time 范围）/ `page` & `page_size`（1.4 分页约定）
- **排序**：按 collect_time 倒序
- **响应**（bbox 不返回——07-协议 V1.0 不展示，仅存 vision_json；level 为应用层计算值，表内不落）：

```json
{"code":0,"data":{"items":[
  {"result_id":31,"event_type":"huddle","event_label":"扎堆","confidence":0.87,"level":"mid","collect_time":"...","pen_name":"1号圈","device_sn":"CAM001"}]}}
```

---

## 八、告警

### 8.1 GET /alert/list?status=pending&type=sensor&farm_id=1&from=2026-10-16&page=1&page_size=20

- **V1.7 参数补齐**：`farm_id` 按场收敛（数字大屏单场视图用；不属于本人的场 → 40302）；
  `from=yyyy-MM-dd` 按**东八区自然日**起算，非法格式 42201（此前大屏「今日新增」是在返回的前 8 条里
  客户端筛，属抽样口径，与同面板「待处理」用的服务端 `total` 不同源，两个数并排显示会互相打脸）
- 类型筛选参数名就是 `type`（07 节曾误加 `alias="alert_type"`，导致前端筛选静默失效，V1.7 已去 alias）

```json
{"code":0,"data":{"total":2,"items":[
  {"alert_id":88,"alert_type":"sensor","risk_level":"mid",
   "alert_content":"1号圈温度 33.2℃，超过阈值上限 28℃（连续 3 次超标）",
   "status":"pending","create_time":"...","pen_name":"1号育肥圈"}]}}
```

### 8.2 POST /alert/{alert_id}/handle

```json
// 置已读：{"action":"acknowledge"}
// 已处理：{"action":"resolve","note":"已开风机通风降温"}
// 判为误报：{"action":"false_alarm","note":"传感器进水"}   // 后端已实装，前端暂无入口（见 08）
```
- **请求体字段名是 `note`（V1.7 修正）**：本文档 V1.6 及以前写的是 `handled_note`，后端实现读的是 `note`，
  前端照文档实现 → 真后端下「标记已处理」恒定 42201「处理备注必填」（mock 内核照文档读 handled_note，
  所以 370 条 .smoke 全绿，缺陷只在切真后暴露）。**响应**侧字段仍是 `handled_note`（那是库列名），
  请求/响应不同名这一点必须记住
- resolve 无 note 返回 42201；处理动作落台账

---

## 九、AI 对话（SSE）

### 9.1 POST /agent/chat

```json
// 请求（pen_id 可选，V1.2 补圈舍定位；individual_id 可选，V1.8 补个体绑定）
{"session_id":5,"message":"3号圈的小猪最近咳嗽，吃料也少了","pen_id":3}
{"session_id":6,"message":"这头 cough 两天了","individual_id":12}
```

- **圈舍定位规则（V1.2）**：请求带 pen_id 时校验归属后直接使用；不带时——用户只有一个圈舍则自动使用，多圈舍由意图识别槽位抽取（如"3号圈"），抽不出则 SSE 先发一条澄清提问，不臆测执行（**V1.4：数据查询子图同样适用，且槽位两级匹配——先本人圈、未命中全场库+归属校验，命中他人圈舍转越权话术**）
- **个体绑定（V1.8，F14）**：`individual_id` 与 `pen_id` 同在开流前校验（流已开始就无法再返回 JSON 错误码）——
  个体不存在/已删档 40401，非本人名下 40302，与显式 `pen_id` 不同圈 42201。
  校验通过后**圈舍以该个体所在圈为准**（话里另提别的圈也不改挂载点），上下文行追加
  `· 个体 P1-M001（公 · 40 日龄）`，disease 台账 `record_ext` 回写 `tag_no`/`individual_id`，
  摘要带耳标；`done` 事件与消息 `meta` 回填 `individual{individual_id,ear_tag_no,pen_name}`
- **消息长度（V1.4）**：message 超过 500 字截断按前 500 字解析（EX-D11，问诊/查询统一）
- **data_query 分支（V1.4，承接 F11）**：意图 data_query 走只读工具白名单四把（存栏汇总/传感器摘要/告警查询/复核状态，白名单外无实现函数=物理禁写）；回答数值 100% 来自查询结果（附录 A 约束 5 工程保证）；done 事件扩展 `denied:true`（越权话术轮，40302 语义传递）与 `clarify:true`（澄清轮）；写指令（data_query 内或 unknown+祈使模式兜底检测）拒绝并引导对应页面，不执行任何写操作
- 响应：`text/event-stream`，事件序列见架构文档第六节
- 服务端副作用：诊断/用药落台账；high 风险创建 vet_review(pending) 并中断工作流
- 错误：`event: error`，`data: {"code":50002,"message":"知识库暂无该病症资料，建议联系兽医"}`
- 预留（PRD 3.5.9）：V1.1 起 multipart 支持 image 字段，V1.0 契约不堵死

### 9.2 GET /agent/sessions

```json
{"code":0,"data":{"items":[{"session_id":5,"title":"小猪咳嗽问诊","update_time":"..."}]}}
```

### 9.3 GET /agent/sessions/{session_id}/messages

```json
{"code":0,"data":{"items":[{"role":"user","content":"...","create_time":"..."},
                            {"role":"assistant","content":"...","meta":{"risk_level":"mid"}}]}}
```

- **`meta.individual`（V1.8）**：绑定个体的轮次为 `{"individual_id":12,"ear_tag_no":"P1-M001","pen_name":"育肥A圈"}`，
  未绑定则不带该键。历史回看靠它复原"已绑定"标记与「记入个体档案」弹窗的预选

---

## 十、台账

### 10.1 GET /record/list?farm_id=1&pen_id=&record_type=&from=&to=&page=1

- items 每条：通用列（时间、圈舍、类型、摘要）+ record_ext 动态字段
- **`ext_columns` 契约（V1.7 明确）**：`[{"field":"male_delta","label":"公变动"}, ...]`，
  列序先按 `EXT_FIELD_LABELS` 定义序、未登记的键按首次出现追加（与 mock 的兜底规则同构）。
  后端 V1.6 实际返回的是裸字符串数组，前端按对象消费 → 表头空白、单元格全「—」；
  **V1.8 按实测补齐**：`smoke_contract` 现断言"出现的每个动态列都有中文列名"（兜底规则下 `label == field` 即漏登记），
  据此补了 AI 问诊/用药/复核的全部键（含 `tag_no`→耳标号、`individual_id`→个体编号）
- **`farm_id` 现为强制口径**：不属于本人的场 → 40302。此前后端签名里没有该参数，FastAPI 静默丢弃，
  台账页的「养殖场」筛选点了没反应、导出恒含全部场

### 10.2 GET /record/export?farm_id=1&from=&to=&record_type=

- **响应是 JSON 信封，不是文件流（V1.7 修正 V1.6 遗留描述）**：
  `{"filename":"ledger-20261016-101200.csv","mime":"text/csv","content":"<CSV 正文，含 UTF-8 BOM>"}`
  由前端包装 Blob 下载（C-15 已收口：`USE_MOCK=false` 的旧文件流分支会静默拿到 undefined 文件名）。
  若 F04 决策 2 将来真要 xlsx，需同时改本文档、后端生成器与 `record-api.ts` 三处
- 扩展列规则：按当前筛选范围内实际出现的扩展字段动态生成列，列序同 10.1，表头取 `label`

---

## 十一、兽医复核（role=vet）

### 11.1 GET /vet/pending-cases

```json
{"code":0,"data":{"items":[
  {"review_id":3,"alert_id":88,"waiting_hours":26.5,"timeout_level":"warn",
   "original_diagnosis":"疑似支原体肺炎","farm_name":"文彬家庭农场","pen_name":"1号圈"}]}}
```
- waiting_hours ≥24 → timeout_level "warn"（置顶标红）；≥48 后端已自动降级，不再出现

### 11.2 POST /vet/review

```json
// 确认
{"review_id":3,"action":"approve","review_opinion":"同意诊断与用药"}
// 驳回修改
{"review_id":3,"action":"reject","review_opinion":"建议先排除流感",
 "revised_diagnosis":"猪流行性感冒","revised_med":"双黄连可溶性粉拌料"}
```
- 副作用：恢复对应 LangGraph checkpoint，按（修改后）方案继续落台账
- **药库校验（V1.2）**：revised_med 必须在 medicine_lib 中存在且非违禁（is_forbidden=false），否则返回 42201 并提示；校验底线对人和 AI 一视同仁（PRD 3.5.8）

---

## 十二、后台管理（role=admin）

| 接口 | 说明 |
|------|------|
| CRUD /admin/user | 兽医账号创建、启用/禁用 |
| CRUD /admin/species | 物种与环境阈值、气候规则、生命周期参数 |
| CRUD /admin/variety | 品种与饲喂参数、台账扩展字段定义 |
| CRUD /admin/medicine | 兽药库（含违禁标记） |
| CRUD /admin/feed-nutrition | 饲料营养库 |
| POST /admin/kb/upload | 上传知识库整理稿 markdown（记录 source/organizer），触发切片重建索引 |

---

## 十三、WebSocket

**/ws/alerts?token=JWT**

服务端下发消息：

```json
{"event":"alert","data":{"alert_id":88,"alert_type":"sensor","risk_level":"mid",
 "alert_content":"1号圈温度超标","pen_name":"1号育肥圈","create_time":"..."}}
{"event":"heartbeat","data":{"ts":"..."}}
```

- 客户端 30s 未收到 heartbeat 应断线重连（指数退避 1s→2s→4s→...→30s 上限）

---

## 十四、看板聚合（V1.1 新增）

### GET /dashboard/summary?farm_id=1

```json
{"code":0,"data":{
  "stock":{"male":30,"female":120},
  "feeding_completion":{"planned_kg":500.0,"actual_kg":460.0,"ratio":0.92},
  "avg_temperature":24.6,
  "feed_cost_per_head_day":2.31,
  "pending_alerts":3,
  "today_reminders":2}}
```

- 对应 06 规范 3.2 首页 4 张指标卡 + 提醒/告警汇总条，一次拉取避免前端多次拼接

### GET /feeding/overview?date=2026-10-09&farm_id=1（喂食日历按天汇总；V1.7 补 farm_id）

```json
{"code":0,"data":{"items":[
  {"pen_id":1,"pen_name":"1号育肥圈","planned_kg":100.0,"actual_kg":100.0,"registered":true},
  {"pen_id":2,"pen_name":"2号保育圈","planned_kg":40.0,"actual_kg":null,"registered":false}]}}
```

- registered=false 的圈舍前端高亮待办（06 规范 3.6）；数据来源 feeding_record 当日记录 + feed-plan 计划量

---

## 十五、其他

- `GET /health`：`{"pg":true,"redis":true,"mqtt":true,"milvus":true}`
- 气象：`GET /weather?farm_id=1` → 风险等级 + 建议 + `data_time`（缓存时间标注，EX-02）

## 十六、个体耳标档案（F14 新增，role=farmer）

> 强关联对账：在栏个体数 == 圈舍存栏快照；快照仍只由 stock_change 流水驱动。
> 耳标号场内唯一（含全部状态个体）。详细口径见《23-功能PRD-F14》。

### 16.1 列表与对账

`GET /individual/list?pen_id=&status=active|sold|dead|removed|all&keyword=&page=&page_size=`

```json
{
  "code": 0,
  "data": {
    "summary": { "stock_total": 5, "active_total": 5, "diff": 0 },
    "pens": [ { "pen_id": 1, "pen_name": "育肥A圈", "stock_total": 5, "active_total": 5, "diff": 0 } ],
    "total": 5, "page": 1, "page_size": 20,
    "items": [ {
      "individual_id": 1, "ear_tag_no": "P1-M001", "pen_id": 1, "pen_name": "育肥A圈",
      "gender": "male", "gender_label": "公", "variety_code": "pig_fattening", "variety_name": "育肥猪",
      "birth_date": null, "source": "初始建档", "status": "active", "status_label": "在栏",
      "exit_note": "", "exit_time": null, "remark": "", "create_time": "2026-10-14T08:00:00.000Z"
    } ]
  }
}
```

### 16.2 单个建档（差额头寸内补录）

`POST /individual` `{ pen_id, ear_tag_no, gender: "male"|"female", birth_date?, source?, remark? }`
→ `{ individual_id, ear_tag_no }`。该性别在栏个体 ≥ 快照数时 42201（提示走调整存栏流水）。

### 16.3 编辑 / 纠错删档

- `PUT /individual/{id}` `{ ear_tag_no?, birth_date?, source?, remark? }`（性别与圈舍归属不可改）
- `DELETE /individual/{id}`：仅在栏个体可删（纠错）；已离栏 42201。

### 16.4 一键补齐

`POST /pen/{pen_id}/individuals/auto-fill` `{ prefix? }`
→ `{ created_male, created_female, active_total, stock_total, message }`。
按性别头寸生成 `P{penId}-M/F+序号`（前缀可自定义，序号自动避让已占用号）；只建档案不动存栏，台账留痕。

### 16.5 存栏/转群/出栏强关联字段（4.x/5.4 契约增量）

| 接口 | 新增请求字段 | 校验 |
| --- | --- | --- |
| `POST /pen/{id}/stock-change`（birth/purchase/manual+） | `male_tags[]` / `female_tags[]` | 数量与公母增量一致、场内未占用 |
| `POST /pen/{id}/stock-change`（death/sale/manual−） | `exit_tag_nos[]` | 本圈在栏、公母分布与数量一致 |
| `POST /pen/{id}/transfer` | `tag_nos[]`（必填） | 源圈在栏、分布一致；成功后个体迁移 |
| `POST /pen/{id}/slaughter` | `tag_nos[]`（可选） | 部分出栏必填；整栏出栏缺省自动清档 |

## 十七、个体档案详情（F17 新增，role=farmer）

> 一畜一档：基本信息 + 全生命周期事件时间线 + 体重曲线。数据模型为事件寄存器（individual_event）。
> 流水联动事件（register 建档 / rename 换标 / transfer 转群 / exit 离栏）由系统自动落、只增不删；
> 手动事件（weight 体重 / immunization 免疫驱虫 / treatment 发病诊疗 / note 备注）可纠错删除。

### 17.1 档案详情聚合

`GET /individual/{id}/detail`

```json
{
  "code": 0,
  "data": {
    "individual": { "individual_id": 1, "ear_tag_no": "P1-M001", "gender": "male", "gender_label": "公",
      "pen_name": "育肥A圈", "variety_name": "育肥猪", "birth_date": null, "source": "初始建档",
      "status": "active", "status_label": "在栏", "traits": "白底黑斑", "lineage": "父：长白；母：大白", "...": "..." },
    "events": [ { "event_id": 1, "event_type": "register", "event_type_label": "建档",
      "event_date": "2026-10-14", "payload": { "source": "初始建档" },
      "summary": "来源：初始建档", "manual": false, "create_time": "..." } ],
    "weight_series": [ { "date": "2026-08-15", "weight_kg": 40, "daily_gain": null },
                       { "date": "2026-08-30", "weight_kg": 55, "daily_gain": 1.0 } ]
  }
}
```

### 17.2 记一笔（手动事件）

`POST /individual/{id}/event` `{ event_type, event_date, ...payload }`
- weight：`weight_kg`（0~500）+ `note?`
- immunization：`vaccine_name`（必填）+ `dose?` / `next_date?`
- treatment：`symptom`（必填）+ `diagnosis?` / `medication?` / `outcome?` / `vet_name?`
- note：`content`（必填）
- 校验：event_date ≤ 今天、≥ 出生日期（已填时）；流水类型由系统记录，传入即 42201。

### 17.3 纠错删除

`DELETE /individual/event/{event_id}`：仅手动事件可删；档案事件 42201。

### 17.4 编辑扩展（PUT /individual/{id} 增量）

`traits`（外貌/毛色特征 ≤128）/ `lineage`（血缘备注 ≤128，法规·种畜个体档案要求）；耳标号变更自动落 rename 事件。

---

> 本文档为技术设计参考，具体实施请结合公司合规与法务要求确认。
