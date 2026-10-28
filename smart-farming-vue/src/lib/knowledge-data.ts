// F05：猪病知识库 + 兽药库种子（mock 期静态数据，切真后迁移 Milvus/PostgreSQL）
//
// - MEDICINE_LIB_SEED：字段对齐 02-数据库 3.6 medicine_lib DDL
//   （med_name/usage/withdrawal_period/is_forbidden/contraindication 等），
//   mock-backend 在建库/迁移时灌入 medicineLib 表，admin 后台维护随阶段 10。
//   休药期为演示估算值（参照兽药标签常见区间），真实以药品说明书为准。
//   F07 修正：阿莫西林可溶性粉 28→7、氟苯尼考注射液 14→28，对齐《02-数据库设计》5.4
//   种子与 03-API 5.4 出栏拦截示例原文（EX-11/TC-L07 验收按文档口径执行）。
// - PIG_DISEASE_KB：猪常见病知识条目（人工整理的结构化数据，mock 期按关键词检索，
//   切真时本批条目直接切片灌 Milvus kb_disease 集合——F05 PRD 决策 4，整理零浪费）。
//   条目内容为公开兽医学教材/官方技术规范的一般性知识整理（原创改写），
//   source 标注出处，原文不做二次分发（F05 PRD FR-01 版权约束）。
// - 违禁药清单依据农业农村部公告第 250 号（食品动物中禁止使用的化合物），
//   仅收录名称用于拦截演示，不提供任何使用信息。

// ===== 兽药库种子（medicine_lib，02-数据库 3.6）=====

export interface MedLibSeedItem {
  speciesCode: string;
  diseaseName: string | null;
  medName: string;
  medCode: string | null;
  usage: string;
  withdrawalPeriod: number;
  isForbidden: boolean;
  contraindication: string | null;
  riskLevel: string | null;
}

const PIG = 'pig';
const SHEEP = 'sheep';
const CATTLE = 'cattle';

export const MEDICINE_LIB_SEED: MedLibSeedItem[] = [
  { speciesCode: PIG, diseaseName: '呼吸道疾病', medName: '阿莫西林可溶性粉', medCode: 'AMX-WS', usage: '混饮，每 1L 水 60mg，连用 3~5 日（标签用法示例）', withdrawalPeriod: 7, isForbidden: false, contraindication: '对青霉素过敏动物禁用', riskLevel: null },
  { speciesCode: PIG, diseaseName: '呼吸道疾病', medName: '氟苯尼考注射液', medCode: 'FFC-INJ', usage: '肌注，一次量每 kg 体重 20mg，每 48 小时一次，连用 2 次（标签用法示例）', withdrawalPeriod: 28, isForbidden: false, contraindication: '繁殖期母畜慎用', riskLevel: null },
  { speciesCode: PIG, diseaseName: '呼吸道疾病', medName: '延胡索酸泰妙菌素预混剂', medCode: 'TIA-PRE', usage: '混饲，每 1000kg 饲料 100g，连用 5~7 日（标签用法示例）', withdrawalPeriod: 3, isForbidden: false, contraindication: '禁与莫能菌素、盐霉素联用', riskLevel: null },
  { speciesCode: PIG, diseaseName: '呼吸道疾病', medName: '盐酸多西环素可溶性粉', medCode: 'DOX-WS', usage: '混饮，每 1L 水 25mg，连用 3~5 日（标签用法示例）', withdrawalPeriod: 28, isForbidden: false, contraindication: '长期使用易致二重感染', riskLevel: null },
  { speciesCode: PIG, diseaseName: '呼吸道疾病', medName: '头孢噻呋钠注射液', medCode: 'CEF-INJ', usage: '肌注，一次量每 kg 体重 3~5mg，每日一次，连用 3 日（标签用法示例）', withdrawalPeriod: 1, isForbidden: false, contraindication: '对头孢菌素过敏动物禁用', riskLevel: null },
  { speciesCode: PIG, diseaseName: '消化道疾病', medName: '硫酸黏菌素预混剂', medCode: 'COL-PRE', usage: '混饲，每 1000kg 饲料 40g，连用 5 日（标签用法示例）', withdrawalPeriod: 7, isForbidden: false, contraindication: '肾功能不全动物慎用', riskLevel: null },
  { speciesCode: PIG, diseaseName: '消化道疾病', medName: '乙酰甲喹注射液', medCode: 'MEQ-INJ', usage: '肌注，一次量每 kg 体重 2.5mg，每日 2 次，连用 3 日（标签用法示例）', withdrawalPeriod: 30, isForbidden: false, contraindication: '超量易致中毒，严格按标签', riskLevel: null },
  { speciesCode: PIG, diseaseName: '全身感染', medName: '磺胺间甲氧嘧啶钠注射液', medCode: 'SMM-INJ', usage: '肌注，一次量每 kg 体重 50mg，每日 1~2 次，连用 3~5 日（标签用法示例）', withdrawalPeriod: 28, isForbidden: false, contraindication: '充足饮水防结晶尿，首次量加倍', riskLevel: null },
  { speciesCode: PIG, diseaseName: '全身感染', medName: '恩诺沙星注射液', medCode: 'ENR-INJ', usage: '肌注，一次量每 kg 体重 2.5mg，每日 1~2 次，连用 3~5 日（标签用法示例）', withdrawalPeriod: 10, isForbidden: false, contraindication: '幼龄动物慎用（软骨影响）', riskLevel: null },
  { speciesCode: PIG, diseaseName: '寄生虫病', medName: '伊维菌素注射液', medCode: 'IVM-INJ', usage: '皮下注射，一次量每 kg 体重 0.3mg（标签用法示例）', withdrawalPeriod: 28, isForbidden: false, contraindication: '柯利犬等敏感动物禁用；孕畜慎用', riskLevel: null },
  { speciesCode: PIG, diseaseName: '寄生虫病', medName: '阿苯达唑片', medCode: 'ALB-TAB', usage: '内服，一次量每 kg 体重 5~10mg（标签用法示例）', withdrawalPeriod: 14, isForbidden: false, contraindication: '孕畜禁用', riskLevel: null },
  { speciesCode: PIG, diseaseName: '细菌感染', medName: '青霉素钾注射液', medCode: 'PEN-INJ', usage: '肌注，一次量每 kg 体重 2~3 万单位，每日 2 次（标签用法示例）', withdrawalPeriod: 1, isForbidden: false, contraindication: '对青霉素过敏动物禁用，现配现用', riskLevel: null },
  { speciesCode: PIG, diseaseName: '产科用药', medName: '葡萄糖酸钙注射液', medCode: 'CA-INJ', usage: '静注，一次量 50~150ml，缓慢注射（标签用法示例）', withdrawalPeriod: 0, isForbidden: false, contraindication: '静注过快易致心律失常', riskLevel: null },
  // —— 羊（F12 FR-05 横向复制：条目为公开兽药标签用法示例整理，休药期以药品说明书为准）——
  { speciesCode: SHEEP, diseaseName: '呼吸道疾病', medName: '延胡索酸泰妙菌素预混剂（羊）', medCode: 'TIA-PRE-S', usage: '混饲，每 1000kg 饲料 100g，连用 5~7 日（标签用法示例）', withdrawalPeriod: 3, isForbidden: false, contraindication: '禁与莫能菌素、盐霉素联用', riskLevel: null },
  { speciesCode: SHEEP, diseaseName: '呼吸道疾病', medName: '氟苯尼考注射液（羊）', medCode: 'FFC-INJ-S', usage: '肌注，一次量每 kg 体重 20mg，每 48 小时一次，连用 2 次（标签用法示例）', withdrawalPeriod: 28, isForbidden: false, contraindication: '繁殖期母畜慎用', riskLevel: null },
  { speciesCode: SHEEP, diseaseName: '寄生虫病', medName: '伊维菌素注射液（羊）', medCode: 'IVM-INJ-S', usage: '皮下注射，一次量每 kg 体重 0.2mg（标签用法示例）', withdrawalPeriod: 28, isForbidden: false, contraindication: '孕畜慎用', riskLevel: null },
  { speciesCode: SHEEP, diseaseName: '全身感染', medName: '磺胺间甲氧嘧啶钠注射液（羊）', medCode: 'SMM-INJ-S', usage: '肌注，一次量每 kg 体重 50mg，每日 1~2 次，连用 3~5 日（标签用法示例）', withdrawalPeriod: 28, isForbidden: false, contraindication: '充足饮水防结晶尿', riskLevel: null },
  { speciesCode: SHEEP, diseaseName: '寄生虫病', medName: '阿苯达唑片（羊）', medCode: 'ALB-TAB-S', usage: '内服，一次量每 kg 体重 10~15mg（标签用法示例）', withdrawalPeriod: 14, isForbidden: false, contraindication: '孕畜禁用', riskLevel: null },
  { speciesCode: SHEEP, diseaseName: '细菌感染', medName: '青霉素钾注射液（羊）', medCode: 'PEN-INJ-S', usage: '肌注，一次量每 kg 体重 2~3 万单位，每日 2 次（标签用法示例）', withdrawalPeriod: 1, isForbidden: false, contraindication: '对青霉素过敏动物禁用，现配现用', riskLevel: null },
  // —— 牛（F12 FR-05 横向复制：条目为公开兽药标签用法示例整理，休药期以药品说明书为准）——
  { speciesCode: CATTLE, diseaseName: '呼吸道疾病', medName: '头孢噻呋钠注射液（牛）', medCode: 'CEF-INJ-C', usage: '肌注，一次量每 kg 体重 2.2mg，每日一次，连用 3~5 日（标签用法示例）', withdrawalPeriod: 1, isForbidden: false, contraindication: '对头孢菌素过敏动物禁用', riskLevel: null },
  { speciesCode: CATTLE, diseaseName: '呼吸道疾病', medName: '氟苯尼考注射液（牛）', medCode: 'FFC-INJ-C', usage: '肌注，一次量每 kg 体重 20mg，每 48 小时一次，连用 2 次（标签用法示例）', withdrawalPeriod: 28, isForbidden: false, contraindication: '繁殖期母畜慎用', riskLevel: null },
  { speciesCode: CATTLE, diseaseName: '寄生虫病', medName: '阿苯达唑片（牛）', medCode: 'ALB-TAB-C', usage: '内服，一次量每 kg 体重 10~15mg（标签用法示例）', withdrawalPeriod: 14, isForbidden: false, contraindication: '孕畜禁用', riskLevel: null },
  { speciesCode: CATTLE, diseaseName: '全身感染', medName: '恩诺沙星注射液（牛）', medCode: 'ENR-INJ-C', usage: '肌注，一次量每 kg 体重 2.5mg，每日 1~2 次，连用 3~5 日（标签用法示例）', withdrawalPeriod: 10, isForbidden: false, contraindication: '幼龄动物慎用（软骨影响）', riskLevel: null },
  // —— 以下为违禁药（is_forbidden=true），仅用于拦截演示（TC-G07）——
  { speciesCode: PIG, diseaseName: '促生长（违禁）', medName: '盐酸克伦特罗（瘦肉精）', medCode: 'CLB-BAN', usage: '食品动物禁用', withdrawalPeriod: 0, isForbidden: true, contraindication: '食品动物中禁止使用（农业农村部公告第 250 号）', riskLevel: 'high' },
  { speciesCode: PIG, diseaseName: '促生长（违禁）', medName: '沙丁胺醇', medCode: 'SAL-BAN', usage: '食品动物禁用', withdrawalPeriod: 0, isForbidden: true, contraindication: '食品动物中禁止使用（农业农村部公告第 250 号）', riskLevel: 'high' },
  { speciesCode: PIG, diseaseName: '抗感染（违禁）', medName: '氯霉素', medCode: 'CAP-BAN', usage: '食品动物禁用', withdrawalPeriod: 0, isForbidden: true, contraindication: '食品动物中禁止使用（农业农村部公告第 250 号）', riskLevel: 'high' },
  { speciesCode: PIG, diseaseName: '抗感染（违禁）', medName: '呋喃唑酮', medCode: 'FZD-BAN', usage: '食品动物禁用', withdrawalPeriod: 0, isForbidden: true, contraindication: '食品动物中禁止使用（农业农村部公告第 250 号）', riskLevel: 'high' },
];

// ===== 猪病知识条目（kb_disease 集合的 mock 期形态）=====

// F16：'general' 供管理员上传的知识文档使用（种子条目仍用五分类）
export type DiseaseCategory = 'respiratory' | 'digestive' | 'reproductive' | 'neurological' | 'skin' | 'general';

export interface DiseaseEntry {
  /** 物种归属（F12 FR-05：读侧按圈舍物种过滤，物种隔离不跨命中） */
  speciesCode: 'pig' | 'cattle' | 'sheep';
  diseaseId: string;
  diseaseName: string;
  category: DiseaseCategory;
  /** 症状关键词：用户消息命中越多得分越高（mock 期关键词检索口径） */
  symptomKeywords: string[];
  riskLevel: 'low' | 'mid' | 'high';
  /** 母畜专属病：仅性别构成含母畜的圈舍输出（主 PRD 3.5.2 / TC-G09） */
  femaleOnly?: boolean;
  /** 推荐药（必须能在 MEDICINE_LIB_SEED 中找到且非违禁，F05 PRD FR-06 库内校验） */
  medNames: string[];
  /** 轻量饲料/饲养管理建议（FR-07，附依据） */
  feedAdvice: string;
  /** 溯源出处（FR-01，公开出版物/官方技术规范整理稿） */
  source: string;
}

export const DISEASE_KB: DiseaseEntry[] = [
  // —— 呼吸系统 ——
  { speciesCode: PIG, diseaseId: 'pig-mhp', diseaseName: '猪支原体肺炎（喘气病）', category: 'respiratory', symptomKeywords: ['咳嗽', '喘气', '气喘', '腹式呼吸', '干咳', '呛咳', '不吃食', '减食'], riskLevel: 'mid', medNames: ['延胡索酸泰妙菌素预混剂', '盐酸多西环素可溶性粉'], feedAdvice: '保持圈舍通风良好、降低氨气浓度，减少粉尘刺激；避免饲养密度过高。', source: '《猪病学》呼吸系统疾病章节（公开出版物合规整理）' },
  { speciesCode: PIG, diseaseId: 'pig-app', diseaseName: '猪传染性胸膜肺炎', category: 'respiratory', symptomKeywords: ['呼吸困难', '高热', '发烧', '犬坐', '皮肤发紫', '发绀', '口鼻流血'], riskLevel: 'high', medNames: ['氟苯尼考注射液', '头孢噻呋钠注射液'], feedAdvice: '发病期间减少应激，保证清洁饮水；改善通风并降低饲养密度。', source: '《猪病学》呼吸系统疾病章节（公开出版物合规整理）' },
  { speciesCode: PIG, diseaseId: 'pig-flu', diseaseName: '猪流行性感冒', category: 'respiratory', symptomKeywords: ['突发高热', '全群咳嗽', '流鼻涕', '流泪', '精神沉郁', '扎堆'], riskLevel: 'mid', medNames: ['阿莫西林可溶性粉'], feedAdvice: '保温通风兼顾，供水充足；可在饮水中添加电解质多维缓解应激。', source: '《猪病学》呼吸系统疾病章节（公开出版物合规整理）' },
  { speciesCode: PIG, diseaseId: 'pig-csf', diseaseName: '猪瘟（古典猪瘟）', category: 'respiratory', symptomKeywords: ['高热不退', '皮肤出血点', '出血斑', '脓性眼屎', '便秘腹泻交替', '走路摇摆'], riskLevel: 'high', medNames: [], feedAdvice: '无治疗价值，须立即隔离并报告当地动物疫病预防控制机构；禁止自行用药掩盖症状。', source: '农业农村部《猪瘟防治技术规范》（官方公开规范整理）' },
  // —— 消化系统 ——
  { speciesCode: PIG, diseaseId: 'pig-scour', diseaseName: '仔猪黄白痢（大肠杆菌病）', category: 'digestive', symptomKeywords: ['仔猪腹泻', '黄痢', '白痢', '黄色稀粪', '脱水', '消瘦'], riskLevel: 'mid', medNames: ['头孢噻呋钠注射液', '硫酸黏菌素预混剂'], feedAdvice: '母猪乳汁质量是关键：加强母猪饲喂与乳头清洁；仔猪保温箱保持干燥。', source: '《猪病防治手册》消化道疾病章节（公开出版物合规整理）' },
  { speciesCode: PIG, diseaseId: 'pig-ped', diseaseName: '猪流行性腹泻/传染性胃肠炎', category: 'digestive', symptomKeywords: ['水样腹泻', '喷射状腹泻', '呕吐', '全群腹泻', '快速脱水'], riskLevel: 'high', medNames: [], feedAdvice: '无特效抗病毒药：重点是补液防脱水（饮水添加口服补液盐）、保温；停料减料减轻肠道负担。', source: '《猪病学》消化系统疾病章节（公开出版物合规整理）' },
  { speciesCode: PIG, diseaseId: 'pig-dysentery', diseaseName: '猪痢疾（密螺旋体病）', category: 'digestive', symptomKeywords: ['血痢', '便血', '黏液血便', '红色粪便', '里急后重'], riskLevel: 'mid', medNames: ['乙酰甲喹注射液'], feedAdvice: '粪污及时清理并消毒；治愈后坚持全进全出，防止带菌猪混群。', source: '《猪病防治手册》消化道疾病章节（公开出版物合规整理）' },
  { speciesCode: PIG, diseaseId: 'pig-paratyphoid', diseaseName: '仔猪副伤寒（沙门氏菌病）', category: 'digestive', symptomKeywords: ['腹泻', '皮肤紫斑', '败血症', '持续下痢', '耳根发紫'], riskLevel: 'mid', medNames: ['氟苯尼考注射液', '磺胺间甲氧嘧啶钠注射液'], feedAdvice: '断奶应激期注意饲料过渡；料槽清洁，防止霉变饲料。', source: '《猪病防治手册》消化道疾病章节（公开出版物合规整理）' },
  { speciesCode: PIG, diseaseId: 'pig-ulcer', diseaseName: '胃溃疡', category: 'digestive', symptomKeywords: ['呕吐', '黑便', '柏油样便', '贫血', '苍白', '磨牙'], riskLevel: 'low', medNames: [], feedAdvice: '检查饲料粒度过细问题，适当提高粗纤维；减少应激，保证饲料新鲜无霉变。', source: '《猪病学》消化系统疾病章节（公开出版物合规整理）' },
  // —— 繁殖障碍（母畜专属）——
  { speciesCode: PIG, diseaseId: 'pig-sow-paralysis', diseaseName: '母猪产后瘫痪', category: 'reproductive', symptomKeywords: ['产后瘫痪', '站不起来', '后肢无力', '四肢发软', '产后站不起来'], riskLevel: 'mid', femaleOnly: true, medNames: ['葡萄糖酸钙注射液'], feedAdvice: '妊娠后期与哺乳期日粮补钙补磷并调整钙磷比例；增加光照与适度运动。', source: '《猪病学》繁殖障碍与产科疾病章节（公开出版物合规整理）' },
  { speciesCode: PIG, diseaseId: 'pig-mastitis', diseaseName: '母猪乳房炎', category: 'reproductive', symptomKeywords: ['乳房红肿', '乳房硬块', '拒哺', '乳汁带血', '乳房发热'], riskLevel: 'mid', femaleOnly: true, medNames: ['阿莫西林可溶性粉', '头孢噻呋钠注射液'], feedAdvice: '哺乳期控制催料节奏防乳汁过浓；保持产床干燥清洁，必要时对仔猪实行寄养。', source: '《猪病防治手册》产科疾病章节（公开出版物合规整理）' },
  { speciesCode: PIG, diseaseId: 'pig-abortion', diseaseName: '母猪流产/繁殖障碍', category: 'reproductive', symptomKeywords: ['流产', '死胎', '早产', '木乃伊胎', '返情'], riskLevel: 'high', femaleOnly: true, medNames: [], feedAdvice: '立即隔离流产母猪并对流产胎儿做无害化处理；记录流产日龄供兽医排查病因（蓝耳/伪狂犬/细小等）。', source: '农业农村部相关疫病防治技术规范（官方公开规范整理）' },
  // —— 神经系统 ——
  { speciesCode: PIG, diseaseId: 'pig-strep', diseaseName: '猪链球菌病（脑膜炎型）', category: 'neurological', symptomKeywords: ['转圈', '抽搐', '尖叫', '倒地划水', '关节肿', '跛行', '高热'], riskLevel: 'high', medNames: ['磺胺间甲氧嘧啶钠注射液', '青霉素钾注射液'], feedAdvice: '外伤是主要感染途径：剪牙断尾规范操作、地面防滑防尖锐物；病猪单独护理。', source: '《猪病学》神经系统疾病章节（公开出版物合规整理）' },
  { speciesCode: PIG, diseaseId: 'pig-edema', diseaseName: '仔猪水肿病', category: 'neurological', symptomKeywords: ['眼睑水肿', '脸部肿胀', '共济失调', '走路摇晃', '突然发病', '断奶后发病'], riskLevel: 'high', medNames: ['恩诺沙星注射液', '磺胺间甲氧嘧啶钠注射液'], feedAdvice: '断奶后避免突然换料与暴食，饲料过渡 5~7 天；降低日粮蛋白可减轻肠道负担。', source: '《猪病防治手册》仔猪疾病章节（公开出版物合规整理）' },
  // —— 皮肤与寄生虫 ——
  { speciesCode: PIG, diseaseId: 'pig-mange', diseaseName: '猪疥螨病', category: 'skin', symptomKeywords: ['蹭痒', '皮肤结痂', '耳部擦伤', '脱毛', '皮肤增厚', '蹭墙'], riskLevel: 'low', medNames: ['伊维菌素注射液', '阿苯达唑片'], feedAdvice: '全群同步驱虫并结合环境杀虫（栏舍喷淋），间隔 7~10 天重复一次效果更佳。', source: '《猪病防治手册》寄生虫病章节（公开出版物合规整理）' },
  { speciesCode: PIG, diseaseId: 'pig-erysipelas', diseaseName: '猪丹毒', category: 'skin', symptomKeywords: ['菱形疹块', '皮肤方块红斑', '高热', '疹块凸起'], riskLevel: 'high', medNames: ['青霉素钾注射液'], feedAdvice: '发病猪隔离治疗；高温高湿季节加强防蚊蝇与栏舍消毒。', source: '《猪病学》皮肤疾病章节（公开出版物合规整理）' },
  { speciesCode: PIG, diseaseId: 'pig-eczema', diseaseName: '湿疹/过敏性皮炎', category: 'skin', symptomKeywords: ['红疹', '皮肤红斑', '瘙痒', '丘疹', '无热'], riskLevel: 'low', medNames: [], feedAdvice: '排查饲料霉变与垫料潮湿；保持皮肤清洁干燥，防蚊蝇叮咬。', source: '《猪病防治手册》普通病章节（公开出版物合规整理）' },
  // —— 一般性 ——
  { speciesCode: PIG, diseaseId: 'pig-cold', diseaseName: '普通感冒（风寒）', category: 'respiratory', symptomKeywords: ['流清鼻涕', '轻咳', '精神一般', '受凉', '降温后发病', '不吃食'], riskLevel: 'low', medNames: ['阿莫西林可溶性粉'], feedAdvice: '重点在防寒保温与防贼风；保证温水供应，减少冷应激。', source: '《猪病防治手册》普通病章节（公开出版物合规整理）' },
  // —— 羊（F12 FR-05 横向复制：公开出版物防治技术资料合规整理，演示口径）——
  { speciesCode: SHEEP, diseaseId: 'sheep-mhp', diseaseName: '羊支原体肺炎', category: 'respiratory', symptomKeywords: ['咳嗽', '气喘', '干咳', '呛咳', '呼吸急促', '流鼻涕', '不吃食', '采食下降'], riskLevel: 'mid', medNames: ['延胡索酸泰妙菌素预混剂（羊）'], feedAdvice: '圈舍保温与通风兼顾，降低氨气与粉尘刺激；发病群减少驱赶应激，保证清洁温水。', source: '《羊病学》呼吸系统疾病章节（公开出版物合规整理）' },
  { speciesCode: SHEEP, diseaseId: 'sheep-pasteurellosis', diseaseName: '羊巴氏杆菌病', category: 'respiratory', symptomKeywords: ['呼吸困难', '咳嗽', '流鼻涕', '颈部水肿', '咽喉肿胀'], riskLevel: 'high', medNames: ['氟苯尼考注射液（羊）'], feedAdvice: '发病羊立即隔离；气候骤变与运输应激期减少调动，圈舍通风保温兼顾。', source: '《羊病防治手册》呼吸系统疾病章节（公开出版物合规整理）' },
  { speciesCode: SHEEP, diseaseId: 'sheep-enterotoxemia', diseaseName: '羊肠毒血症', category: 'digestive', symptomKeywords: ['突然死亡', '抽搐', '水样腹泻', '痉挛', '死前挣扎'], riskLevel: 'high', medNames: ['磺胺间甲氧嘧啶钠注射液（羊）'], feedAdvice: '立即停喂精料与青绿多汁饲料，转移至干燥处；全群按体质减料并紧急接种（遵兽医方案）。', source: '《羊病防治手册》消化道疾病章节（公开出版物合规整理）' },
  { speciesCode: SHEEP, diseaseId: 'sheep-braxy', diseaseName: '羊快疫', category: 'digestive', symptomKeywords: ['突然死亡', '腹胀', '腹痛', '口吐白沫', '衰竭'], riskLevel: 'high', medNames: ['青霉素钾注射液（羊）'], feedAdvice: '发病急病程短，重点在疫苗预防与气候骤变时的圈舍保暖；病死羊无害化处理。', source: '《羊病防治手册》消化道疾病章节（公开出版物合规整理）' },
  { speciesCode: SHEEP, diseaseId: 'sheep-pox', diseaseName: '羊痘', category: 'skin', symptomKeywords: ['痘疹', '皮肤痘', '结节', '结痂', '流鼻涕'], riskLevel: 'high', medNames: ['青霉素钾注射液（羊）'], feedAdvice: '隔离病羊防止接触传播；皮肤痘疹涂碘甘油防继发感染，圈舍彻底消毒。', source: '《羊病防治手册》皮肤疾病章节（公开出版物合规整理）' },
  { speciesCode: SHEEP, diseaseId: 'sheep-mange', diseaseName: '羊痒螨病', category: 'skin', symptomKeywords: ['蹭痒', '脱毛', '皮肤结痂', '蹭墙', '皮肤增厚'], riskLevel: 'low', medNames: ['伊维菌素注射液（羊）'], feedAdvice: '全群同步药浴或注射驱虫并结合环境杀虫，间隔 7~10 天重复一次。', source: '《羊病防治手册》寄生虫病章节（公开出版物合规整理）' },
  { speciesCode: SHEEP, diseaseId: 'sheep-worm', diseaseName: '羊胃肠道线虫病', category: 'digestive', symptomKeywords: ['消瘦', '贫血', '腹泻', '不吃食', '食欲差', '颌下水肿', '被毛粗乱'], riskLevel: 'mid', medNames: ['阿苯达唑片（羊）'], feedAdvice: '放牧草场轮牧与驱虫计划结合；春秋两季定期驱虫，粪便堆肥发酵。', source: '《羊病防治手册》寄生虫病章节（公开出版物合规整理）' },
  { speciesCode: SHEEP, diseaseId: 'sheep-ecthyma', diseaseName: '羊口疮（传染性脓疱）', category: 'skin', symptomKeywords: ['口疮', '唇部水疱', '口角结痂', '采食困难', '流涎'], riskLevel: 'mid', medNames: ['青霉素钾注射液（羊）'], feedAdvice: '羔羊口腔破损是主要感染途径；给予柔软饲草，唇部痂皮清理后涂碘甘油。', source: '《羊病防治手册》皮肤疾病章节（公开出版物合规整理）' },
  // —— 牛（F12 FR-05 横向复制：公开出版物防治技术资料合规整理，演示口径）——
  { speciesCode: CATTLE, diseaseId: 'cattle-pasteurellosis', diseaseName: '牛巴氏杆菌病（出血性败血症）', category: 'respiratory', symptomKeywords: ['高热', '呼吸困难', '流鼻涕', '颈部水肿', '腹泻'], riskLevel: 'high', medNames: ['头孢噻呋钠注射液（牛）'], feedAdvice: '发病牛立即隔离；应激期（运输/气候骤变）减少调动，圈舍通风保温兼顾。', source: '《牛病防治手册》呼吸系统疾病章节（公开出版物合规整理）' },
  { speciesCode: CATTLE, diseaseId: 'cattle-fever', diseaseName: '牛流行热', category: 'respiratory', symptomKeywords: ['突发高热', '流泪', '呼吸困难', '跛行', '流鼻涕'], riskLevel: 'mid', medNames: ['恩诺沙星注射液（牛）'], feedAdvice: '蚊蝇季节加强防蚊措施；发病期保证饮水与电解质补充，轻症数日可自愈。', source: '《牛病学》病毒性疾病章节（公开出版物合规整理）' },
  { speciesCode: CATTLE, diseaseId: 'cattle-atony', diseaseName: '牛前胃弛缓', category: 'digestive', symptomKeywords: ['反刍减少', '食欲差', '不吃食', '消瘦', '磨牙', '左肷蠕动弱'], riskLevel: 'low', medNames: [], feedAdvice: '排查饲料突变与霉变；给予优质干草促进反刍，适当牵引运动按摩瘤胃。', source: '《牛病防治手册》消化系统疾病章节（公开出版物合规整理）' },
  { speciesCode: CATTLE, diseaseId: 'cattle-bloat', diseaseName: '牛瘤胃臌气', category: 'digestive', symptomKeywords: ['腹部膨大', '左肷膨隆', '呼吸困难', '停止采食', '不安'], riskLevel: 'high', medNames: [], feedAdvice: '急性臌气属急症须立即处理（套管针穿刺由兽医操作）；避免大量采食幼嫩豆科牧草。', source: '《牛病防治手册》消化系统疾病章节（公开出版物合规整理）' },
  { speciesCode: CATTLE, diseaseId: 'cattle-mastitis', diseaseName: '牛乳腺炎', category: 'reproductive', symptomKeywords: ['乳房红肿', '乳汁异常', '奶量下降', '乳房发热', '拒哺'], riskLevel: 'mid', medNames: ['头孢噻呋钠注射液（牛）'], feedAdvice: '挤奶前后药浴乳头、挤奶设备定期校准；干奶期注入干奶药（遵兽医方案）。', source: '《牛病防治手册》产科疾病章节（公开出版物合规整理）' },
  { speciesCode: CATTLE, diseaseId: 'cattle-liverfluke', diseaseName: '牛肝片吸虫病', category: 'digestive', symptomKeywords: ['消瘦', '贫血', '颌下水肿', '间歇性腹泻', '被毛粗乱'], riskLevel: 'mid', medNames: ['阿苯达唑片（牛）'], feedAdvice: '潮湿草场是感染源：排水与轮牧结合，秋冬定期驱虫。', source: '《牛病防治手册》寄生虫病章节（公开出版物合规整理）' },
];

// ===== 检索与校验（mock 问诊管道使用，纯函数便于单测）=====

const RISK_ORDER = { low: 1, mid: 2, high: 3 } as const;

export interface DiseaseSearchResult {
  /** 命中且适用当前圈舍的条目（按得分降序，最多 5 条） */
  matched: DiseaseEntry[];
  /** 命中但为母畜专属病且当前圈舍不含母畜（TC-G09 不适用提示） */
  notApplicable: DiseaseEntry[];
}

/** 关键词检索：按圈舍物种过滤后，按消息命中症状关键词计分，返回 top_k=5（01-架构 5.2 口径）。
 *  F16：extraEntries 为管理员上传的知识文档映射条目（物种过滤由调用方完成，此处兜底再滤一次），
 *  与种子条目同池同口径计分——mock 期"上传即可检索"，切真后对应 Milvus upsert 重建索引。 */
export function searchDiseases(message: string, speciesCode: string, hasFemale: boolean, topK = 5, extraEntries: DiseaseEntry[] = []): DiseaseSearchResult {
  const pool = [...DISEASE_KB.filter((e) => e.speciesCode === speciesCode), ...extraEntries.filter((e) => e.speciesCode === speciesCode)];
  const scored = pool.map((entry) => {
    const score = entry.symptomKeywords.reduce(
      (acc, kw) => (message.includes(kw) ? acc + 1 : acc),
      0,
    );
    return { entry, score };
  });
  const hits = scored.filter((s) => s.score > 0).sort((a, b) => b.score - a.score);
  const matched: DiseaseEntry[] = [];
  const notApplicable: DiseaseEntry[] = [];
  for (const { entry } of hits) {
    if (entry.femaleOnly && !hasFemale) notApplicable.push(entry);
    else matched.push(entry);
  }
  return { matched: matched.slice(0, topK), notApplicable: notApplicable.slice(0, topK) };
}

/** 取已匹配条目中的最高风险等级（诊断分级口径） */
export function maxRisk(entries: DiseaseEntry[]): 'low' | 'mid' | 'high' {
  return entries.reduce<'low' | 'mid' | 'high'>(
    (acc, e) => (RISK_ORDER[e.riskLevel] > RISK_ORDER[acc] ? e.riskLevel : acc),
    'low',
  );
}
