<!-- 畜牧养殖综合展示 3D（毕业设计演示用，Three.js 程序化建模）
     生猪栏舍 / 肉牛棚舍 / 肉羊高床羊舍：按用户圈舍实际物种出对应栏舍，一次只显示一栋居中。
     每舍配齐喂料 / 饮水 / 清粪设备；清粪刮板带往复动画。
     单场景封装于组件内，卸载时释放 renderer / controls / 监听器。 -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { PhotoSceneConfig } from '@/api/scene-api';
import type { BarnFocus, BarnInstance, HotspotKey, Species, WeatherMode } from './types';

const props = defineProps<{
  /** 用户圈舍列表（每项=一栋：名称+物种；未建圈舍时 undefined → 默认单栋生猪） */
  barns?: BarnInstance[];
  focus?: BarnFocus | null;
  /** F15 拍照建场景：按识别配置生成定制栏舍；null = 模板场景 */
  photoConfig?: PhotoSceneConfig | null;
  /** F08 气象联动：晴/多云/雨（null=数据未就绪，按晴天渲染） */
  weather?: WeatherMode | null;
  /** 环控联动：温度超阈值（≥30℃）的圈舍名列表 → 对应栏舍红温脉冲 */
  hotPens?: string[] | null;
}>();

const ALL_SPECIES: Species[] = ['pig', 'cattle', 'sheep'];
// 切换栏舍时的相机落点（单舍居中视角）
const VIEW_PRESET: Record<Species, { pos: [number, number, number]; tgt: [number, number, number] }> = {
  pig: { pos: [7, 10.5, 20], tgt: [0, 2, 0] },
  cattle: { pos: [7, 10.5, 21], tgt: [0, 2, 0] },
  sheep: { pos: [7, 10.5, 19], tgt: [0, 2.4, 0] },
};

// 告警定位锚点：每栋栏舍内 环境/设备/监控/栏位 四类关键点位（F06 告警 3D 定位）
const HOTSPOTS: Record<Species, Record<HotspotKey, [number, number, number]>> = {
  pig: { env: [0, 1.5, -3.0], device: [-2.2, 2.2, -4.8], cam: [7.5, 3.8, 9.2], pen: [-4.0, 0.9, 1.85] },
  cattle: { env: [0, 3.1, 0.3], device: [0, 0.9, 3.0], cam: [7.5, 3.8, 9.2], pen: [0, 0.8, -0.2] },
  sheep: { env: [0, 2.6, 0], device: [0, 1.7, 2.85], cam: [7.5, 3.8, 9.2], pen: [-2.4, 1.8, -0.5] },
};
const DEFAULT_LABEL: Record<Species, string> = {
  pig: '生猪栏舍', cattle: '肉牛棚舍', sheep: '肉羊高床羊舍',
};
/* ---------- 设施命名感应区（点击拾取用）：位置+半径，半径越小优先级越高 ---------- */
type Zone = { n: string; p: [number, number, number]; r: number };
const BARN_ZONES: Record<Species, Zone[]> = {
  pig: [
    { n: '料塔', p: [-2.2, 1.5, -4.8], r: 1.6 },
    { n: '料塔', p: [2.2, 1.5, -4.8], r: 1.6 },
    { n: '塞盘式自动料线', p: [0, 1.85, 0.85], r: 1.1 },
    { n: '塞盘式自动料线', p: [0, 1.85, -0.85], r: 1.1 },
    { n: '鸭嘴式饮水管线', p: [0, 1.7, 3.2], r: 1.0 },
    { n: '鸭嘴式饮水管线', p: [0, 1.7, -3.2], r: 1.0 },
    { n: '负压风机', p: [-3, 1.4, -3.5], r: 1.0 },
    { n: '负压风机', p: [0, 1.4, -3.5], r: 1.0 },
    { n: '负压风机', p: [3, 1.4, -3.5], r: 1.0 },
    { n: '湿帘降温墙', p: [-2, 0.95, 3.52], r: 1.5 },
    { n: '粪沟+刮板清粪', p: [0, 0.13, 3.26], r: 0.9 },
    { n: '粪沟+刮板清粪', p: [0, 0.13, -3.26], r: 0.9 },
    { n: '舍外集粪池', p: [0, 0.3, -4.6], r: 1.5 },
    { n: '仔猪保温箱', p: [-1.4, 0.5, -2.1], r: 0.9 },
  ],
  cattle: [
    { n: 'TMR 饲槽', p: [0, 0.4, 3.55], r: 1.5 },
    { n: '颈枷采食位', p: [0, 1.0, 2.9], r: 1.2 },
    { n: '舍内吊扇', p: [-3.5, 3.3, 0.3], r: 1.3 },
    { n: '舍内吊扇', p: [0, 3.3, 0.3], r: 1.3 },
    { n: '舍内吊扇', p: [3.5, 3.3, 0.3], r: 1.3 },
    { n: '自动饮水槽', p: [-3.2, 0.4, -3.4], r: 1.2 },
    { n: '自动饮水槽', p: [3.2, 0.4, -3.4], r: 1.2 },
    { n: '粪沟+刮板清粪', p: [0, 0.13, -4.1], r: 1.0 },
    { n: '入口消毒槽', p: [5, 0.1, 3.4], r: 1.2 },
    { n: '运动场', p: [0, 0.2, 6.5], r: 2.6 },
  ],
  sheep: [
    { n: '草架+长条料槽', p: [0, 1.6, 2.85], r: 1.6 },
    { n: '高架饲喂走道', p: [0, 1.3, 3.95], r: 1.2 },
    { n: '自动饮水碗', p: [0, 1.5, -3.05], r: 1.0 },
    { n: '储水桶', p: [-4.3, 1.8, -2.4], r: 0.9 },
    { n: '储水桶', p: [4.3, 1.8, -2.4], r: 0.9 },
    { n: '药浴池', p: [3.2, 0.4, 6.6], r: 1.1 },
    { n: '上床斜坡', p: [3.5, 0.7, 5.9], r: 1.3 },
    { n: '干草垛', p: [-5.6, 0.8, -5.5], r: 1.2 },
    { n: '围墙运动场', p: [-1.5, 0.2, 6.6], r: 2.4 },
    { n: '架空漏缝地板', p: [0, 1.3, -0.2], r: 3.2 },
  ],
};
const PER_BARN = 6;   // 每栋长舍栏位数（双列 3 跨），超出并行加排
const ROW_GAP = 16;   // 多排时排间距（≥舍深的 2 倍 + 场区设施带，真实场舍间距口径）

const SITE_ZONES: Zone[] = [
  { n: '大门车辆消毒池', p: [6, 0.3, 10.6], r: 2.6 },
  { n: '车辆洗消棚', p: [6, 1.8, 12.4], r: 2.0 },
  { n: '气象监测杆', p: [-7, 2.75, 10.2], r: 1.6 },
  { n: '视频监控杆', p: [7.5, 2.1, 9.2], r: 1.6 },
  { n: '储粪罐', p: [-15, 1.6, -7], r: 2.0 },
  { n: '固液分离棚', p: [-10.5, 1.2, -7], r: 2.0 },
  { n: '水塔', p: [15.5, 3.4, -7], r: 1.8 },
  { n: '青贮窖', p: [12, 1.2, -6.2], r: 2.4 },
  { n: '干草棚', p: [-18.5, 1.8, -6.4], r: 2.6 },
];

const LEGEND: Record<Species, { color: string; title: string; desc: string }> = {
  pig: {
    color: '#7c9ab8', title: '生猪栏舍',
    desc: '料塔+塞盘式自动料线（喂料）｜鸭嘴式饮水管线（饮水）｜漏缝栏位+双侧刮板清粪｜负压风机+湿帘纵向通风｜仔猪保温箱',
  },
  cattle: {
    color: '#8a8f98', title: '肉牛棚舍',
    desc: 'TMR 饲槽+颈枷采食位（喂料）｜自动饮水槽+饮水碗（饮水）｜后走道刮板清粪｜吊扇防暑+屋脊通风孔+运动场',
  },
  sheep: {
    color: '#b59a72', title: '肉羊高床羊舍',
    desc: '饲喂走道+草架料槽（喂料）｜自动饮水碗+储水桶（饮水）｜架空漏缝地板+床下刮板清粪｜围墙运动场+药浴池',
  },
};
const SITE_LEGEND =
  '场区配套：硬化净道 · 大门消毒池/车辆洗消棚 · 气象监测杆 · 视频监控杆 · 储粪罐+固液分离棚 · 青贮窖 · 干草棚 · 水塔';

// 展示物种：拍照定制优先，否则第一栋圈舍的物种（默认生猪）
const displaySpecies = computed<Species>(() => props.photoConfig?.species ?? props.barns?.[0]?.species ?? 'pig');
const photoMode = computed(() => !!props.photoConfig);
const legend = computed(() => LEGEND[displaySpecies.value]);

const mountEl = ref<HTMLDivElement | null>(null);
const picked = ref<{ name: string; sub: string; x: number; y: number } | null>(null);
const autoRotate = ref(true);
const layoutMode = ref(false); // 布置模式：拖动房舍调整场区布局

function enterLayout(): void {
  layoutMode.value = !layoutMode.value;
  toggleLayoutMode(layoutMode.value);
}

function resetLayout(): void {
  resetBarnLayout();
}

/* ---------- 命令式场景句柄（非响应式：只在 mount 内赋值，watcher 读取） ---------- */
let orbitControls: OrbitControls | null = null;
let focusMarker: THREE.Group | null = null;
let customRef: THREE.Group | null = null;
let buildCustom: ((cfg: PhotoSceneConfig) => THREE.Group) | null = null;
let customAnim: { scrapers: THREE.Mesh[]; fans: THREE.Group[] } = { scrapers: [], fans: [] };
let instAnim: { fans: THREE.Object3D[]; scrapers: THREE.Mesh[] } = { fans: [], scrapers: [] };
let roofMat: THREE.MeshStandardMaterial | null = null;
let instMeta: { off: number; species: Species; name: string; row: number }[] = [];
let pensAnchor: [number, number][] = [];
let barnsBox: { x: number; z: number; half: number; row: number; species: Species }[] = [];
let roofGhost: (on: boolean) => void = () => {};
let ghostOn = false; // 虚化持久状态：布局重建后自动恢复
let applyLayout: (list: BarnInstance[]) => void = () => {};
let hideAll: () => void = () => {};
let disposeScene: (() => void) | null = null;
let pickedTimer = 0;
// 拖动布置：每排长舍当前摆放位（layoutPens 重建时恢复，拖动/告警定位共用）
let rowPos: { x: number; z: number }[] = [];
let rowKeys: string[] = []; // 每排的持久化键（圈名集合）
let toggleLayoutMode: (on: boolean) => void = () => {};
let resetBarnLayout: () => void = () => {};
// 红温联动：每排长舍的可发热材质（墙体/屋面，局部材质不串舍）+ 是否高温
let instHeat: { mats: THREE.MeshStandardMaterial[]; hot: boolean }[] = [];
const goal = {
  pos: new THREE.Vector3(7, 10.5, 20),
  tgt: new THREE.Vector3(0, 2, 0),
  active: false,
};

const pickedStyle = computed(() => {
  const p = picked.value;
  if (!p) return {};
  const w = mountEl.value?.clientWidth ?? 900;
  const h = mountEl.value?.clientHeight ?? 700;
  return {
    left: `${Math.max(8, Math.min(p.x, w - 220))}px`,
    top: `${Math.min(p.y + 10, h - 72)}px`,
  };
});

// 告警定位：切到对应栏舍（定位到具体那一栋）+ 屋顶虚化便于看舍内 + 标记移到锚点
function syncFocus(): void {
  const focus = props.focus ?? null;
  const setRoofGhost = (on: boolean) => { ghostOn = on; roofGhost(on); };
  const marker = focusMarker;
  if (!focus) {
    if (marker) marker.visible = false;
    setRoofGhost(false);
    // 退出定位：恢复拍照定制（若有）或模板场景
    if (customRef) customRef.visible = photoMode.value;
    else applyLayout(props.barns ?? []);
    return;
  }
  if (customRef) customRef.visible = false; // 定位优先展示模板栏舍
  setRoofGhost(true);                       // 屋顶虚化，看清告警源
  const list = props.barns ?? [];
  applyLayout(list);                        // 先布局（会重置相机目标）
  if (marker) {
    const idx = focus.penName ? list.findIndex((b) => b.name === focus.penName) : -1;
    const anchors = pensAnchor;
    const an = idx >= 0 && anchors.length === list.length ? anchors[idx] : (anchors[0] ?? [0, 0]);
    const row = Math.floor(Math.max(0, idx) / PER_BARN);
    const rp = rowPos[row] ?? { x: 0, z: -row * ROW_GAP }; // 拖动后的房舍实际位置
    const sp = (idx >= 0 ? list[idx]?.species : focus.species) ?? 'pig';
    let mx: number, my: number, mz: number;
    if (focus.hotspot === 'pen') { mx = an[0] + rp.x; my = 1.0; mz = an[1] + rp.z; }
    else if (focus.hotspot === 'cam') { mx = 7.5; my = 3.8; mz = 9.2; }
    else {
      const [hx, hy, hz] = HOTSPOTS[sp]?.[focus.hotspot] ?? HOTSPOTS.pig[focus.hotspot];
      mx = an[0] + rp.x + hx * 0.7; my = hy; mz = hz + rp.z;
    }
    marker.position.set(mx, my, mz);
    marker.visible = true;
    goal.pos.set(mx + 4.5, my + 3.5, mz + 7.5);
    goal.tgt.set(mx, my, mz);
    goal.active = true;
  }
  if (orbitControls) orbitControls.autoRotate = false;
  autoRotate.value = false;
}

// F15 拍照建场景：按识别配置重建定制栏舍（清空时恢复模板场景）
function syncPhoto(): void {
  if (customRef) {
    const old = customRef;
    old.parent?.remove(old);
    old.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh) { m.geometry.dispose(); (m.material as THREE.Material).dispose(); }
    });
    customRef = null;
  }
  customAnim = { scrapers: [], fans: [] };
  const cfg = props.photoConfig;
  if (!cfg || !buildCustom) {
    applyLayout(props.barns ?? []);
    return;
  }
  hideAll();                                   // 模板三舍全部隐藏
  customRef = buildCustom(cfg);
  const preset = VIEW_PRESET[cfg.species];
  goal.pos.set(preset.pos[0], preset.pos[1], preset.pos[2]);
  goal.tgt.set(preset.tgt[0], preset.tgt[1], preset.tgt[2]);
  goal.active = true;
  if (orbitControls) orbitControls.autoRotate = false;
  autoRotate.value = false;
}

function toggleRotate(): void {
  autoRotate.value = !autoRotate.value;
  if (orbitControls) orbitControls.autoRotate = autoRotate.value;
}

function resetView(): void {
  const c = orbitControls;
  if (!c) return;
  const p = VIEW_PRESET[displaySpecies.value];
  c.object.position.set(p.pos[0], p.pos[1], p.pos[2]);
  c.target.set(p.tgt[0], p.tgt[1], p.tgt[2]);
  c.update();
}

onMounted(() => {
  const mount = mountEl.value;
  if (!mount) return;

  /* ---------- 场景基础 ---------- */
  const scene = new THREE.Scene();
  // 天色/雾在「天气系统」段统一设置（随晴/多云/雨渐变）

  const camera = new THREE.PerspectiveCamera(42, mount.clientWidth / mount.clientHeight, 0.1, 300);
  camera.position.set(7, 10.5, 20);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(mount.clientWidth, mount.clientHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  mount.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.target.set(0, 2, 0);
  controls.maxPolarAngle = Math.PI / 2 - 0.06;
  controls.minDistance = 6;
  controls.maxDistance = 80;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.7;
  orbitControls = controls;

  /* ---------- 灯光（强度随天气模式平滑过渡） ---------- */
  const hemiLight = new THREE.HemisphereLight(0xffffff, 0x9aa48f, 0.85);
  scene.add(hemiLight);
  const keyLight = new THREE.DirectionalLight(0xfff4e0, 1.7);
  keyLight.position.set(20, 30, 16);
  keyLight.castShadow = true;
  keyLight.shadow.mapSize.set(2048, 2048);
  Object.assign(keyLight.shadow.camera, { left: -32, right: 32, top: 26, bottom: -26, far: 90 });
  scene.add(keyLight);
  const fill = new THREE.DirectionalLight(0xbfd4ff, 0.35);
  fill.position.set(-18, 10, -14);
  scene.add(fill);

  /* ---------- 天气系统（F08 气象联动）：晴/多云/雨 → 天色+雾+光照渐变，雨天粒子 ---------- */
  const WEATHER_STYLE: Record<WeatherMode, { sky: number; fogNear: number; keyInt: number; hemiInt: number }> = {
    sunny:  { sky: 0xdfe7ee, fogNear: 70, keyInt: 1.7, hemiInt: 0.85 },
    cloudy: { sky: 0xb4bdc6, fogNear: 55, keyInt: 1.0, hemiInt: 0.68 },
    rain:   { sky: 0x8f99a3, fogNear: 42, keyInt: 0.72, hemiInt: 0.58 },
  };
  const skyColor = new THREE.Color(0xdfe7ee);          // 当前天色（逐帧向目标渐变）
  const tmpSky = new THREE.Color();                    // 渐变目标色暂存
  scene.background = skyColor;
  const fog = new THREE.Fog(0xdfe7ee, 70, 160);
  scene.fog = fog;
  let weatherTarget = WEATHER_STYLE.sunny;

  // 雨滴粒子：LineSegments（每滴上下两端，带风斜），落到底回弹顶部循环
  const RAIN_COUNT = 620;
  const RAIN_AREA = { x: 52, z: 38, top: 30 };
  const rainPos = new Float32Array(RAIN_COUNT * 6);
  const resetDrop = (i: number, init = false): void => {
    const k = i * 6;
    const x = (Math.random() - 0.5) * 2 * RAIN_AREA.x;
    const z = (Math.random() - 0.5) * 2 * RAIN_AREA.z;
    const y = init ? Math.random() * RAIN_AREA.top : RAIN_AREA.top + Math.random() * 4;
    const len = 0.85 + Math.random() * 0.5;
    rainPos[k] = x; rainPos[k + 1] = y; rainPos[k + 2] = z;
    rainPos[k + 3] = x - 0.12; rainPos[k + 4] = y - len; rainPos[k + 5] = z;
  };
  for (let i = 0; i < RAIN_COUNT; i++) resetDrop(i, true);
  const rainGeo = new THREE.BufferGeometry();
  rainGeo.setAttribute('position', new THREE.BufferAttribute(rainPos, 3));
  const rain = new THREE.LineSegments(
    rainGeo,
    new THREE.LineBasicMaterial({ color: 0xa9c6dc, transparent: true, opacity: 0.5 }),
  );
  rain.visible = false;
  rain.frustumCulled = false;
  scene.add(rain);

  const applyWeather = (m: WeatherMode | null | undefined): void => {
    weatherTarget = WEATHER_STYLE[m ?? 'sunny'];
    rain.visible = m === 'rain';
  };
  applyWeather(props.weather);

  /* ---------- 地面 ---------- */
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(180, 120),
    new THREE.MeshStandardMaterial({ color: 0xd4d8cc, roughness: 0.95 }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);

  /* ---------- 通用工具 ---------- */
  const boxGeo = new THREE.BoxGeometry(1, 1, 1);
  const cylGeo = new THREE.CylinderGeometry(1, 1, 1, 20);
  const coneGeo = new THREE.ConeGeometry(1, 1, 20);
  const mat = (c: number, m = 0.05, r = 0.8) =>
    new THREE.MeshStandardMaterial({ color: c, metalness: m, roughness: r });

  const box = (
    parent: THREE.Object3D, w: number, h: number, d: number,
    x: number, y: number, z: number, material: THREE.Material,
  ) => {
    const ms = new THREE.Mesh(boxGeo, material);
    ms.scale.set(w, h, d); ms.position.set(x, y, z);
    parent.add(ms); return ms;
  };
  const cyl = (
    parent: THREE.Object3D, r: number, h: number,
    x: number, y: number, z: number, material: THREE.Material, rot?: [number, number, number],
  ) => {
    const ms = new THREE.Mesh(cylGeo, material);
    ms.scale.set(r, h, r); ms.position.set(x, y, z);
    if (rot) ms.rotation.set(rot[0], rot[1], rot[2]);
    parent.add(ms); return ms;
  };
  const cone = (
    parent: THREE.Object3D, r: number, h: number,
    x: number, y: number, z: number, material: THREE.Material,
  ) => {
    const ms = new THREE.Mesh(coneGeo, material);
    ms.scale.set(r, h, r); ms.position.set(x, y, z);
    parent.add(ms); return ms;
  };
  // 栅栏线（立柱 + 双横杆）
  const fenceLine = (
    parent: THREE.Object3D, x1: number, z1: number, x2: number, z2: number,
    baseY: number, m: THREE.Material,
  ) => {
    const dx = x2 - x1, dz = z2 - z1, len = Math.hypot(dx, dz);
    const n = Math.max(2, Math.round(len / 2) + 1);
    for (let i = 0; i < n; i++) {
      const t = i / (n - 1);
      box(parent, 0.07, 1.1, 0.07, x1 + dx * t, baseY + 0.55, z1 + dz * t, m);
    }
    const ang = Math.atan2(dz, dx);
    for (const ry of [0.38, 0.86]) {
      const r = box(parent, len, 0.05, 0.05, x1 + dx / 2, baseY + ry, z1 + dz / 2, m);
      r.rotation.y = -ang;
    }
  };

  /* ---------- 配色 ---------- */
  const wallM = mat(0xf4f1e8, 0, 0.85);    // 米白墙体
  const roofM = mat(0x8f9aa3, 0.15, 0.55); // 灰蓝屋面
  roofMat = roofM;
  const concM = mat(0xc9ccc2, 0, 0.9);     // 混凝土
  const metalM = mat(0x9ba3ab, 0.7, 0.3);  // 镀锌钢
  const darkM = mat(0x55606a, 0.6, 0.4);   // 深钢件
  const pipeM = mat(0xc7ccd1, 0.6, 0.35);  // 料线管
  const woodM = mat(0xb59a72, 0, 0.8);     // 木材（羊床板条）
  const feedG = mat(0x8a9a4d, 0, 0.9);     // 饲料（青贮/颗粒）
  const feedY = mat(0xd9a441, 0, 0.7);     // 料盘/玉米料
  const waterM = mat(0x3f86c9, 0.1, 0.5);  // 饮水设备
  const waterIn = mat(0x8fc3ec, 0.05, 0.25); // 水面
  const chanM = mat(0x44584b, 0, 0.7);     // 粪沟
  const slatM = mat(0x9aa0a0, 0, 0.9);     // 漏缝地板
  const fanM = mat(0x3d4750, 0.5, 0.4);    // 风机/叶轮
  const padM = mat(0xa8c4cc, 0.05, 0.85);  // 湿帘（波纹纸垫）
  const roadM = mat(0xbdbab1, 0, 0.92);    // 场区硬化道路
  const tankM = mat(0x3e5c50, 0.2, 0.5);   // 储粪罐（墨绿玻璃钢）
  const hayM = mat(0xd3b46a, 0, 0.9);      // 干草捆
  const pigSkin = mat(0xe8a9a0, 0, 0.7);   // 猪皮粉
  const cowSkin = mat(0xe6e1d6, 0, 0.75);  // 牛皮白
  const cowSpot = mat(0x8a5f43, 0, 0.75);  // 牛棕斑
  const woolM = mat(0xf1ece2, 0, 0.95);    // 羊毛
  const faceM = mat(0x4a423c, 0, 0.7);     // 羊头/蹄深色
  const box2M = mat(0xe8d9a8, 0, 0.8);     // 仔猪保温箱

  /* ---------- 低多边形动物建模 ---------- */
  const makePig = (s = 1) => {
    const g = new THREE.Group();
    g.userData.isAnimal = true;
    const body = new THREE.Mesh(cylGeo, pigSkin);
    body.scale.set(0.26, 0.5, 0.26); body.rotation.z = Math.PI / 2; // 卧倒圆柱当躯干
    body.position.y = 0.3 * s;
    g.add(body);
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.2 * s, 12, 10), pigSkin);
    head.position.set(0.48 * s, 0.34 * s, 0); g.add(head);
    const snout = new THREE.Mesh(cylGeo, mat(0xd68f85, 0, 0.7));
    snout.scale.set(0.09 * s, 0.08 * s, 0.09 * s); snout.rotation.z = Math.PI / 2;
    snout.position.set(0.63 * s, 0.32 * s, 0); g.add(snout);
    for (const ez of [-0.09, 0.09]) {
      const ear = new THREE.Mesh(coneGeo, pigSkin);
      ear.scale.set(0.06 * s, 0.1 * s, 0.06 * s);
      ear.position.set(0.42 * s, 0.5 * s, ez * s); g.add(ear);
    }
    for (const [lx, lz] of [[0.28, -0.12], [0.28, 0.12], [-0.28, -0.12], [-0.28, 0.12]]) {
      const leg = new THREE.Mesh(cylGeo, pigSkin);
      leg.scale.set(0.055 * s, 0.22 * s, 0.055 * s);
      leg.position.set(lx * s, 0.11 * s, lz * s); g.add(leg);
    }
    return g;
  };
  const makeCattle = () => {
    const g = new THREE.Group();
    g.userData.isAnimal = true;
    const body = new THREE.Mesh(boxGeo, cowSkin);
    body.scale.set(1.15, 0.62, 0.52); body.position.y = 0.75; g.add(body);
    const spot1 = new THREE.Mesh(boxGeo, cowSpot);
    spot1.scale.set(0.4, 0.64, 0.545); spot1.position.set(-0.28, 0.75, 0); g.add(spot1);
    const spot2 = new THREE.Mesh(boxGeo, cowSpot);
    spot2.scale.set(0.26, 0.64, 0.545); spot2.position.set(0.3, 0.75, 0); g.add(spot2);
    const head = new THREE.Mesh(boxGeo, cowSkin);
    head.scale.set(0.34, 0.34, 0.28); head.position.set(0.72, 0.98, 0); g.add(head);
    for (const hz of [-0.1, 0.1]) {
      const horn = new THREE.Mesh(coneGeo, mat(0xd8cfc0, 0.1, 0.6));
      horn.scale.set(0.045, 0.12, 0.045); horn.rotation.z = -0.5;
      horn.position.set(0.66, 1.2, hz); g.add(horn);
    }
    for (const [lx, lz] of [[0.42, -0.18], [0.42, 0.18], [-0.42, -0.18], [-0.42, 0.18]]) {
      const leg = new THREE.Mesh(cylGeo, faceM);
      leg.scale.set(0.08, 0.48, 0.08); leg.position.set(lx, 0.24, lz); g.add(leg);
    }
    return g;
  };
  const makeSheep = (s = 1) => {
    const g = new THREE.Group();
    g.userData.isAnimal = true;
    const wool = new THREE.Mesh(new THREE.SphereGeometry(0.26 * s, 12, 10), woolM);
    wool.scale.set(1.3, 1, 0.95); wool.position.y = 0.4 * s; g.add(wool);
    const head = new THREE.Mesh(boxGeo, faceM);
    head.scale.set(0.16 * s, 0.16 * s, 0.13 * s);
    head.position.set(0.34 * s, 0.46 * s, 0); g.add(head);
    for (const [lx, lz] of [[0.12, -0.1], [0.12, 0.1], [-0.12, -0.1], [-0.12, 0.1]]) {
      const leg = new THREE.Mesh(cylGeo, faceM);
      leg.scale.set(0.035 * s, 0.24 * s, 0.035 * s);
      leg.position.set(lx * s, 0.12 * s, lz * s); g.add(leg);
    }
    return g;
  };

  /* ==========================================================
     左侧 · 生猪栏舍（封闭式，双列栏位 + 中央走道）
     ========================================================== */
  const pig = new THREE.Group();
  scene.add(pig);

  box(pig, 11.6, 0.16, 7.6, 0, 0.08, 0, concM);                    // 地坪
  box(pig, 11, 2.6, 0.18, 0, 1.3, -3.41, wallM);                   // 后墙
  box(pig, 0.18, 2.6, 7, -5.41, 1.3, 0, wallM);                    // 山墙×2
  box(pig, 0.18, 2.6, 7, 5.41, 1.3, 0, wallM);
  for (let x = -4.9; x <= 4.91; x += 2.45) box(pig, 0.18, 2.6, 0.18, x, 1.3, 3.41, wallM); // 前立柱
  box(pig, 11, 0.3, 0.18, 0, 2.45, 3.41, wallM);                   // 前上梁
  // 双坡屋面
  {
    const th = Math.atan(1.4 / 3.5), sl = Math.sqrt(1.4 * 1.4 + 3.5 * 3.5) + 0.6;
    box(pig, 11.9, 0.12, sl, 0, 3.3, 1.75, roofM).rotation.x = th;
    box(pig, 11.9, 0.12, sl, 0, 3.3, -1.75, roofM).rotation.x = -th;
    box(pig, 11.9, 0.1, 0.26, 0, 4.02, 0, roofM);                  // 屋脊
  }
  // 山墙三角封板
  {
    const s = new THREE.Shape();
    s.moveTo(-3.5, 0); s.lineTo(3.5, 0); s.lineTo(0, 1.4); s.lineTo(-3.5, 0);
    const g = new THREE.ExtrudeGeometry(s, { depth: 0.16, bevelEnabled: false });
    const g1 = new THREE.Mesh(g, wallM); g1.rotation.y = Math.PI / 2; g1.position.set(5.5, 2.6, 0); pig.add(g1);
    const g2 = new THREE.Mesh(g, wallM); g2.rotation.y = -Math.PI / 2; g2.position.set(-5.5, 2.6, 0); pig.add(g2);
  }
  // 双列栏位（4 栏 × 2 列）+ 中央走道
  const penXs = [-4.0, -1.4, 1.2, 3.8];
  for (const zc of [1.85, -1.85]) {
    const sg = Math.sign(zc);
    for (const xc of penXs) box(pig, 2.55, 0.06, 2.45, xc, 0.19, zc, slatM); // 栏内漏缝地面
    box(pig, 10.5, 0.05, 0.05, 0, 0.55, sg * 0.6, metalM);         // 走道侧栅栏
    box(pig, 10.5, 0.05, 0.05, 0, 1.0, sg * 0.6, metalM);
    for (const xp of [-5.1, -2.5, 0.1, 2.7, 5.1]) box(pig, 0.06, 1.05, 0.06, xp, 0.6, sg * 0.6, metalM);
    for (const xp of [-2.7, -0.1, 2.5]) {                          // 栏间隔栏
      box(pig, 0.06, 0.05, 2.5, xp, 0.55, zc, metalM);
      box(pig, 0.06, 0.05, 2.5, xp, 1.0, zc, metalM);
      box(pig, 0.06, 1.1, 0.06, xp, 0.6, zc - 1.25, metalM);
      box(pig, 0.06, 1.1, 0.06, xp, 0.6, zc + 1.25, metalM);
    }
    // 喂料：塞盘料线（输料管 + 下料管 + 圆形料盘）
    cyl(pig, 0.05, 10.6, 0, 1.85, sg * 0.85, pipeM, [0, 0, Math.PI / 2]);
    for (const xc of penXs) {
      cyl(pig, 0.035, 0.6, xc, 1.5, sg * 0.85, pipeM);
      cyl(pig, 0.3, 0.12, xc, 0.28, sg * 0.85, feedY);
    }
    // 饮水：鸭嘴式饮水器管线（靠墙）
    cyl(pig, 0.04, 10.6, 0, 1.7, sg * 3.2, pipeM, [0, 0, Math.PI / 2]);
    for (const xc of penXs) box(pig, 0.09, 0.18, 0.12, xc, 1.52, sg * 3.2, waterM);
    // 清粪：双侧粪沟
    box(pig, 10.6, 0.14, 0.4, 0, 0.13, sg * 3.26, chanM);
  }
  const pigScraperA = box(pig, 0.5, 0.3, 0.38, 0, 0.28, 3.26, darkM);
  const pigScraperB = box(pig, 0.5, 0.3, 0.38, 0, 0.28, -3.26, darkM);
  pigScraperA.userData.isScraper = true; pigScraperA.userData.off = 0;
  pigScraperB.userData.isScraper = true; pigScraperB.userData.off = Math.PI;
  // 室外料塔 ×2 + 上料斜管
  const siloM = mat(0xdde1e6, 0.45, 0.35);
  for (const sx of [-2.2, 2.2]) {
    cyl(pig, 0.9, 2.6, sx, 1.3, -4.8, siloM);
    cone(pig, 0.95, 0.7, sx, 2.95, -4.8, siloM);
    for (const [lx, lz] of [[0.5, 0.5], [-0.5, 0.5], [0.5, -0.5], [-0.5, -0.5]])
      cyl(pig, 0.05, 1.0, sx + lx, 0.5, -4.8 + lz, metalM);
    cyl(pig, 0.09, 1.7, sx, 2.35, -4.0, pipeM, [1.85, 0, 0]);      // 斜上料管
  }
  // 环控：后墙负压风机×3（纵向通风出风端，叶轮旋转动画）
  const fanBlades: THREE.Group[] = [];
  for (const fx of [-3, 0, 3]) {
    cyl(pig, 0.55, 0.16, fx, 1.4, -3.52, fanM, [Math.PI / 2, 0, 0]);
    cyl(pig, 0.5, 0.06, fx, 1.4, -3.6, mat(0x2a3238, 0.4, 0.5), [Math.PI / 2, 0, 0]);
    const hub = new THREE.Group();
    hub.position.set(fx, 1.4, -3.56);
    hub.userData.isFanHub = true;
    hub.userData.fanAxis = 'z';
    const bar1 = new THREE.Mesh(boxGeo, mat(0x9aa4ac, 0.6, 0.35));
    bar1.scale.set(0.72, 0.09, 0.04); hub.add(bar1);
    const bar2 = new THREE.Mesh(boxGeo, mat(0x9aa4ac, 0.6, 0.35));
    bar2.scale.set(0.09, 0.72, 0.04); hub.add(bar2);
    pig.add(hub);
    fanBlades.push(hub);
  }
  // 环控：前端湿帘（进风端水帘墙）
  box(pig, 4.2, 1.7, 0.14, -2, 0.95, 3.52, padM);
  box(pig, 4.2, 0.12, 0.2, -2, 1.85, 3.52, metalM);                 // 湿帘上水管
  // 侧墙通风小窗（山墙一排）
  for (const wx of [-3.6, -1.2, 1.2, 3.6]) {
    box(pig, 0.5, 0.4, 0.1, wx, 1.9, -3.46, fanM);
  }
  // 保育：仔猪保温箱（电热板 + 箱体）
  box(pig, 0.85, 0.55, 0.65, -1.4, 0.5, -2.1, box2M);
  box(pig, 0.85, 0.06, 0.1, -1.4, 0.36, -1.72, fanM);               // 出入口门帘
  // 清粪配套：舍外集粪池 + 排污管
  box(pig, 2.4, 0.5, 1.6, 0, 0.25, -4.6, concM);
  box(pig, 2.2, 0.08, 1.4, 0, 0.52, -4.6, mat(0x5a6a60, 0, 0.8));   // 盖板
  cyl(pig, 0.09, 1.3, 0, 0.3, -3.9, pipeM, [Math.PI / 2.4, 0, 0]);  // 排污管
  // 猪只（低多边形）：育肥栏 + 保育栏仔猪
  const pigSpots: [number, number, number][] = [
    [-4.3, 0.9, 1], [-3.6, -0.4, 1], [-3.9, 2.0, 0.85],
    [1.0, 0.5, 1], [1.6, 2.0, 0.9],
  ];
  for (const [px, pz, ps] of pigSpots) {
    const a = makePig(ps);
    a.position.set(px, 0.02, pz);
    a.rotation.y = Math.random() * Math.PI * 2;
    pig.add(a);
  }
  for (const [px, pz] of [[3.5, -1.5], [4.0, -2.2], [3.4, -2.5]]) {
    const p = makePig(0.5);
    p.position.set(px, 0.02, pz);
    p.rotation.y = Math.random() * Math.PI * 2;
    pig.add(p);
  }

  /* ==========================================================
     中间 · 肉牛棚舍（敞开式棚舍 + 采食饲槽 + 颈枷）
     ========================================================== */
  const cow = new THREE.Group();
  scene.add(cow);

  box(cow, 11.6, 0.16, 9.6, 0, 0.08, 0, concM);                    // 地坪
  for (const zc of [4.6, -4.6])
    for (let x = -5.25; x <= 5.26; x += 2.1) box(cow, 0.22, 2.9, 0.22, x, 1.45, zc, wallM); // 立柱
  box(cow, 11.2, 0.22, 0.22, 0, 2.95, 4.6, wallM);                 // 顶部联系梁
  box(cow, 11.2, 0.22, 0.22, 0, 2.95, -4.6, wallM);
  box(cow, 11, 1.0, 0.18, 0, 0.5, -4.5, wallM);                    // 后半墙
  // 双坡屋面
  {
    const th = Math.atan(1.5 / 4.6), sl = Math.sqrt(1.5 * 1.5 + 4.6 * 4.6) + 0.6;
    box(cow, 12.2, 0.12, sl, 0, 3.7, 2.3, roofM).rotation.x = th;
    box(cow, 12.2, 0.12, sl, 0, 3.7, -2.3, roofM).rotation.x = -th;
    box(cow, 12.2, 0.1, 0.26, 0, 4.47, 0, roofM);
  }
  // 喂料：TMR 饲槽 + 青贮料 + 颈枷采食位
  box(cow, 10.8, 0.4, 0.75, 0, 0.32, 3.55, mat(0xd8d3c8, 0, 0.85));
  box(cow, 10.6, 0.08, 0.5, 0, 0.55, 3.55, feedG);
  box(cow, 10.8, 0.08, 0.12, 0, 0.6, 3.2, mat(0xd8d3c8, 0, 0.85));
  for (let x = -5.2; x <= 5.21; x += 0.55) cyl(cow, 0.032, 1.5, x, 0.75, 2.9, metalM); // 颈枷立管
  box(cow, 10.8, 0.06, 0.06, 0, 1.42, 2.9, metalM);                // 颈枷上横杆
  box(cow, 10.8, 0.05, 0.05, 0, 0.5, 2.9, metalM);                 // 颈枷下横杆
  // 卧栏区（垫料地面）
  box(cow, 10.8, 0.06, 5.6, 0, 0.19, -0.1, mat(0xc9b98a, 0, 0.95));
  // 饮水：自动饮水槽 ×2
  for (const wx of [-3.2, 3.2]) {
    box(cow, 1.8, 0.45, 0.65, wx, 0.28, -3.4, waterM);
    box(cow, 1.7, 0.08, 0.55, wx, 0.5, -3.4, waterIn);
  }
  // 清粪：后走道粪沟 + 刮板
  box(cow, 10.8, 0.14, 0.55, 0, 0.13, -4.1, chanM);
  const cowScraper = box(cow, 0.6, 0.32, 0.5, 0, 0.3, -4.1, darkM);
  cowScraper.userData.isScraper = true; cowScraper.userData.off = 1.2;
  // 环控：舍内吊扇×3（夏季防暑，水平旋转动画）
  const ceilFanHubs: THREE.Group[] = [];
  for (const fx of [-3.5, 0, 3.5]) {
    const fg = new THREE.Group();
    cyl(fg, 0.05, 1.0, 0, 0.5, 0, darkM);                          // 吊杆
    const hub = new THREE.Group();
    for (let i = 0; i < 3; i++) {
      const blade = new THREE.Mesh(boxGeo, mat(0xd7dbdf, 0.4, 0.4));
      blade.scale.set(1.3, 0.03, 0.24);
      blade.position.set(0.72, 0, 0);
      const arm = new THREE.Group();
      arm.rotation.y = (i * Math.PI * 2) / 3;
      arm.add(blade);
      hub.add(arm);
    }
    hub.userData.isFanHub = true;
    hub.userData.fanAxis = 'y';
    hub.position.y = 0.02;
    fg.add(hub);
    fg.position.set(fx, 3.3, 0.3);
    cow.add(fg);
    ceilFanHubs.push(hub);
  }
  // 环控：屋脊通风孔×5（高于屋脊，自由启闭）
  for (const vx of [-4.2, -2.1, 0, 2.1, 4.2]) cyl(cow, 0.14, 0.35, vx, 4.6, 0, fanM);
  // 入口消毒槽（前门）
  box(cow, 1.6, 0.12, 0.9, 5.0, 0.1, 3.4, fanM);
  // 运动场（牛舍前方，围栏 + 沙土地面）
  box(cow, 10.6, 0.06, 3.4, 0, 0.19, 6.6, mat(0xd9cba8, 0, 0.95));
  fenceLine(cow, -5.3, 5.0, 5.3, 5.0, 0.16, metalM);               // 与舍相接栏
  fenceLine(cow, -5.3, 5.0, -5.3, 8.3, 0.16, metalM);
  fenceLine(cow, 5.3, 5.0, 5.3, 8.3, 0.16, metalM);
  fenceLine(cow, -5.3, 8.3, 5.3, 8.3, 0.16, metalM);
  // 饮水碗（拴系式，每两头一个，柱上安装）
  for (const wx of [-2.1, 0, 2.1]) box(cow, 0.3, 0.16, 0.24, wx, 0.72, 2.6, waterM);
  // 牛只（低多边形，卧栏区 3 头 + 运动场 2 头）
  const cowSpots: [number, number, number][] = [
    [-3.2, 0.6, 0], [-0.4, 1.1, 1.2], [2.6, 0.2, 2.4],
  ];
  for (const [cx, cz, cr] of cowSpots) {
    const c = makeCattle();
    c.position.set(cx, 0.22, cz);
    c.rotation.y = cr;
    cow.add(c);
  }
  for (const [cx, cz, cr] of [[-2.5, 6.4, -1.4], [2.2, 7.1, 2.6]] as [number, number, number][]) {
    const c = makeCattle();
    c.position.set(cx, 0.22, cz);
    c.rotation.y = cr;
    cow.add(c);
  }

  /* ==========================================================
     右侧 · 肉羊高床羊舍（架空漏缝地板 + 饲喂走道）
     ========================================================== */
  const shp = new THREE.Group();
  scene.add(shp);

  box(shp, 11.2, 0.1, 7.2, 0, 0.05, 0, mat(0x8a857c, 0, 0.92));    // 床下集粪区地面
  // 架空支柱
  for (let x = -5.0; x <= 5.01; x += 2.0)
    for (const zc of [-3.0, 3.0, 3.95]) box(shp, 0.16, 1.3, 0.16, x, 0.65, zc, concM);
  // 高床漏缝地板（板条留缝）
  for (let z = -3.2; z <= 3.21; z += 0.18) box(shp, 10.4, 0.07, 0.09, 0, 1.3, z, woodM);
  // 饲喂走道（高架）
  box(shp, 10.6, 0.08, 1.0, 0, 1.3, 3.95, concM);
  // 四周围栏 + 栏内分圈（3 圈）
  fenceLine(shp, -5.15, -3.2, 5.15, -3.2, 1.37, metalM);
  fenceLine(shp, -5.15, 3.1, 5.15, 3.1, 1.37, metalM);
  fenceLine(shp, -5.15, -3.2, -5.15, 3.1, 1.37, metalM);
  fenceLine(shp, 5.15, -3.2, 5.15, 3.1, 1.37, metalM);
  fenceLine(shp, -1.75, -3.2, -1.75, 3.1, 1.37, metalM);
  fenceLine(shp, 1.75, -3.2, 1.75, 3.1, 1.37, metalM);
  // 喂料：沿走道草架 + 长条料槽
  box(shp, 10.2, 0.28, 0.45, 0, 1.51, 2.85, mat(0xcbb27e, 0, 0.8));
  box(shp, 10.0, 0.06, 0.35, 0, 1.68, 2.85, feedG);
  box(shp, 10.2, 0.5, 0.06, 0, 2.15, 3.02, woodM).rotation.x = 0.45; // 倾斜草架挡板
  // 饮水：储水桶 ×2 + 自动饮水碗 ×3
  for (const bx of [-4.3, 4.3]) cyl(shp, 0.32, 0.85, bx, 1.8, -2.4, waterM);
  for (const wx of [-3, 0, 3]) box(shp, 0.22, 0.15, 0.18, wx, 1.5, -3.05, waterM);
  // 清粪：床下刮板
  const sheepScraper = box(shp, 0.5, 0.25, 0.4, 0, 0.28, 0, darkM);
  sheepScraper.userData.isScraper = true; sheepScraper.userData.off = 2.1;
  // 上床斜坡（右侧）
  box(shp, 1.0, 0.08, 3.15, 3.5, 0.67, 5.9, concM).rotation.x = 0.42;
  box(shp, 0.05, 0.4, 3.2, 2.98, 1.0, 5.9, metalM).rotation.x = 0.42;
  box(shp, 0.05, 0.4, 3.2, 4.02, 1.0, 5.9, metalM).rotation.x = 0.42;
  // 屋面（矮柱支撑双坡）
  for (const xc of [-5.15, 0, 5.15])
    for (const zc of [-3.2, 3.2]) box(shp, 0.16, 1.7, 0.16, xc, 1.37 + 0.85, zc, woodM);
  {
    const th = Math.atan(1.0 / 3.3), sl = Math.sqrt(1.0 * 1.0 + 3.3 * 3.3) + 0.5;
    box(shp, 11.0, 0.1, sl, 0, 3.55, 1.65, roofM).rotation.x = th;
    box(shp, 11.0, 0.1, sl, 0, 3.55, -1.65, roofM).rotation.x = -th;
    box(shp, 11.0, 0.09, 0.24, 0, 4.1, 0, roofM);
  }
  // 运动场（羊舍前方，砖围墙 1.5m——高床羊舍规程标配，面积为羊舍 2~3 倍）
  box(shp, 10.6, 0.06, 3.2, 0, 0.19, 6.7, mat(0xd9cba8, 0, 0.95));   // 沙土地面
  box(shp, 10.6, 1.5, 0.15, 0, 0.75, 8.3, wallM);                    // 南围墙
  box(shp, 0.15, 1.5, 3.3, -5.25, 0.75, 6.7, wallM);                 // 西围墙
  box(shp, 0.15, 1.5, 3.3, 5.25, 0.75, 6.7, wallM);                  // 东围墙
  box(shp, 2.2, 1.5, 0.15, -4.15, 0.75, 5.1, wallM);                 // 北围墙（留中口）
  box(shp, 2.2, 1.5, 0.15, 4.15, 0.75, 5.1, wallM);
  // 药浴池（长方形，深 1m 级，袖珍呈现）
  box(shp, 1.8, 0.6, 1.1, 3.2, 0.3, 6.6, concM);
  box(shp, 1.6, 0.1, 0.9, 3.2, 0.52, 6.6, mat(0x4f7fa8, 0.05, 0.3)); // 药液面
  // 干草垛（舍后堆放区）
  cyl(shp, 0.62, 0.85, -6.3, 0.43, -5.4, hayM);
  cyl(shp, 0.62, 0.85, -4.9, 0.43, -5.6, hayM);
  cyl(shp, 0.62, 0.85, -5.6, 1.28, -5.5, hayM);
  // 羊只（低多边形）：床上 3 只 + 运动场 2 只
  for (const [sx2, sz2] of [[-3.2, -1.2], [-2.2, 0.6], [3.0, 1.4]]) {
    const s2 = makeSheep(1);
    s2.position.set(sx2, 1.37, sz2);
    s2.rotation.y = Math.random() * Math.PI * 2;
    shp.add(s2);
  }
  for (const [sx2, sz2] of [[-1.8, 6.2], [1.2, 7.2]]) {
    const s2 = makeSheep(0.95);
    s2.position.set(sx2, 0.22, sz2);
    s2.rotation.y = Math.random() * Math.PI * 2;
    shp.add(s2);
  }

  /* ==========================================================
     场区配套（真实养殖场标配：生物安全 + 粪污处理 + 物联网感知）
     ========================================================== */
  // 拖动布置持久化：按圈名/设施名存偏移量，刷新/数据重取后自动恢复
  const LS_LAYOUT = 'sf3d-barn-layout-v1';
  type LayoutMap = Record<string, { dx: number; dz: number }>;
  const loadLayout = (): LayoutMap => {
    try { return JSON.parse(localStorage.getItem(LS_LAYOUT) || '{}') as LayoutMap; }
    catch { return {}; }
  };
  let savedLayout = loadLayout();
  const site = new THREE.Group();
  scene.add(site);
  // 场区硬化道路（净道）+ 与舍前连接（道路属地面基础设施，不参与拖动）
  box(site, 64, 0.05, 3, 0, 0.03, 10.6, roadM);
  box(site, 2.6, 0.05, 2.2, 0, 0.03, 8.9, roadM);
  // 布置模式可拖动的场区设施：每设施一个壳组，子网格保持绝对坐标，拖动=整体平移壳组
  const siteFacilities: { g: THREE.Group; name: string; zones: string[] }[] = [];
  const fac = (name: string, zones: string[]): THREE.Group => {
    const g = new THREE.Group();
    site.add(g);
    siteFacilities.push({ g, name, zones });
    return g;
  };
  const facGate = fac('大门消毒区', ['大门车辆消毒池', '车辆洗消棚']);
  const facWeather = fac('气象监测杆', ['气象监测杆']);
  const facCam = fac('视频监控杆', ['视频监控杆']);
  const facManure = fac('粪污处理区', ['储粪罐', '固液分离棚']);
  const facTower = fac('水塔', ['水塔']);
  const facSilo = fac('青贮窖', ['青贮窖']);
  const facHay = fac('干草棚', ['干草棚']);
  // 大门 + 车辆消毒池（规程：长≥4m 深≥0.2m）+ 车辆洗消棚
  for (const gx of [4.4, 7.6]) box(facGate, 0.3, 2.4, 0.3, gx, 1.2, 10.6, darkM);
  box(facGate, 3.5, 0.18, 0.2, 6, 2.5, 10.6, darkM);                    // 门头横梁
  box(facGate, 4.2, 0.18, 2, 6, 0.12, 10.6, mat(0x51707e, 0.05, 0.6));  // 消毒池
  box(facGate, 3.2, 0.06, 1.8, 6, 0.24, 10.6, waterIn);                 // 消毒液面
  for (const px2 of [4.7, 7.3]) box(facGate, 0.16, 3.4, 0.16, px2, 1.7, 12.4, metalM); // 洗消棚柱
  box(facGate, 3.2, 0.14, 2.2, 6, 3.5, 12.4, roofM);                    // 洗消棚顶
  // 气象监测杆（风速杯 + 风向标 + 太阳能板 + 采集箱）
  cyl(facWeather, 0.06, 5.5, -7, 2.75, 10.2, metalM);
  cyl(facWeather, 0.02, 0.9, -7, 5.7, 10.2, metalM, [0, 0, Math.PI / 2]);  // 风速臂
  for (const cy of [-0.3, 0.3]) {
    const cup = new THREE.Mesh(new THREE.SphereGeometry(0.09, 10, 8), fanM);
    cup.position.set(-7 + cy, 5.75, 10.2); facWeather.add(cup);
  }
  box(facWeather, 0.5, 0.12, 0.06, -7.3, 5.15, 10.2, feedY).rotation.z = -0.35; // 风向标尾板
  box(facWeather, 0.9, 0.05, 0.6, -7.35, 4.6, 10.2, mat(0x2f4a63, 0.4, 0.3)).rotation.z = 0.4; // 太阳能板
  box(facWeather, 0.35, 0.5, 0.25, -7, 4.0, 10.2, darkM);                  // 数据采集箱
  // 视频监控杆（球场杆 + 云台相机）
  cyl(facCam, 0.05, 4.2, 7.5, 2.1, 9.2, metalM);
  cyl(facCam, 0.03, 0.7, 7.15, 4.1, 9.2, metalM, [0, 0, Math.PI / 2]);
  box(facCam, 0.22, 0.16, 0.34, 6.85, 4.05, 9.2, fanM);                // 相机
  // 粪污处理区（场区西侧）：储粪罐（防渗密闭）+ 固液分离棚 —— 移出舍间带，避免与多排舍体穿模
  cyl(facManure, 1.5, 3.0, -15, 1.5, -7, tankM);
  cone(facManure, 1.6, 0.8, -15, 3.4, -7, tankM);
  cyl(facManure, 0.1, 2.2, -13.8, 0.9, -6.4, pipeM, [0.5, 0, 0.4]);        // 输送管
  box(facManure, 3.0, 2.2, 0.15, -10.5, 1.1, -8.3, wallM);                 // 分离棚后墙
  box(facManure, 0.15, 2.2, 2.6, -12.0, 1.1, -7.0, wallM);                 // 侧墙
  box(facManure, 0.15, 2.2, 2.6, -9.0, 1.1, -7.0, wallM);
  box(facManure, 3.4, 0.12, 3.0, -10.5, 2.28, -7.0, roofM);
  box(facManure, 1.2, 1.0, 0.8, -10.5, 0.5, -7.0, fanM);                   // 固液分离机
  // 供水：水塔（支架 + 罐体，场区东侧）
  for (const [lx, lz] of [[0.5, 0.5], [-0.5, 0.5], [0.5, -0.5], [-0.5, -0.5]])
    cyl(facTower, 0.07, 2.6, 15.5 + lx, 1.3, -7 + lz, metalM);
  cyl(facTower, 1.0, 1.8, 15.5, 3.5, -7, siloM);
  cone(facTower, 1.05, 0.5, 15.5, 4.65, -7, siloM);
  // 青贮窖（槽式，三面墙一面开，场区东侧）
  box(facSilo, 0.2, 2.4, 4.6, 10.3, 1.2, -6.2, concM);
  box(facSilo, 0.2, 2.4, 4.6, 13.7, 1.2, -6.2, concM);
  box(facSilo, 3.6, 2.4, 0.2, 12, 1.2, -8.4, concM);
  box(facSilo, 3.2, 1.6, 4.2, 12, 0.8, -6.2, feedG);                     // 青贮料堆
  // 干草棚（三面墙一面开 + 草捆堆放，场区西北角）
  box(facHay, 0.2, 3.2, 5.0, -20.4, 1.6, -6.4, wallM);
  box(facHay, 0.2, 3.2, 5.0, -16.6, 1.6, -6.4, wallM);
  box(facHay, 4.0, 3.2, 0.2, -18.5, 1.6, -8.8, wallM);
  box(facHay, 4.4, 0.14, 5.6, -18.5, 3.3, -6.4, roofM);
  for (const [bx2, by2, bz2] of [[-19.4, 0.55, -6.2], [-18.2, 0.55, -6.6], [-17.6, 0.55, -5.9], [-18.8, 1.45, -6.3]] as [number, number, number][]) {
    box(facHay, 1.1, 1.0, 0.8, bx2, by2, bz2, hayM);
  }
  // 设施拖动后点击识别感应区同步位移：记录各感应区出厂位，拖动/复位时按设施名平移
  const siteZonesHome = SITE_ZONES.map((z) => ({ n: z.n, p: [...z.p] as [number, number, number] }));
  const shiftZones = (zones: string[], dx: number, dz: number): void => {
    for (const z of SITE_ZONES) {
      if (!zones.includes(z.n)) continue;
      const home = siteZonesHome.find((h) => h.n === z.n);
      if (!home) continue;
      z.p[0] = home.p[0] + dx;
      z.p[2] = home.p[2] + dz;
    }
  };
  // 恢复用户上次拖动的设施摆放
  for (const f of siteFacilities) {
    const saved = savedLayout[`site:${f.name}`];
    if (saved) {
      f.g.position.set(saved.dx, 0, saved.dz);
      shiftZones(f.zones, saved.dx, saved.dz);
    }
  }

  /* ---------- 栏舍标签（Sprite 文字牌） ---------- */
  const createLabelSprite = (text: string, scale = 1.4) => {
    const c = document.createElement('canvas');
    const ctx = c.getContext('2d')!;
    ctx.font = 'bold 46px "Microsoft YaHei","PingFang SC",sans-serif';
    const w = ctx.measureText(text).width;
    c.width = w + 70; c.height = 78;
    const g = c.getContext('2d')!;
    g.font = 'bold 46px "Microsoft YaHei","PingFang SC",sans-serif';
    g.fillStyle = 'rgba(255,255,255,0.94)';
    g.strokeStyle = '#64748b'; g.lineWidth = 3;
    g.beginPath();
    if (g.roundRect) g.roundRect(3, 3, c.width - 6, c.height - 6, 14);
    else g.rect(3, 3, c.width - 6, c.height - 6);
    g.fill(); g.stroke();
    g.fillStyle = '#1f2937';
    g.textBaseline = 'middle';
    g.fillText(text, 35, c.height / 2 + 2);
    const tex = new THREE.CanvasTexture(c);
    tex.anisotropy = 4;
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: true }));
    sp.userData.isBarnLabel = true;
    sp.scale.set((c.width / 110) * scale, (c.height / 110) * scale, 1);
    return sp;
  };
  const makeLabel = (text: string, y: number, parent: THREE.Object3D, scale = 1.4) => {
    const sp = createLabelSprite(text, scale);
    sp.position.set(0, y, 0);
    parent.add(sp); // 标签随栏舍组显隐
  };
  makeLabel('生猪栏舍', 5.4, pig);
  makeLabel('肉牛棚舍', 5.7, cow);
  makeLabel('肉羊高床羊舍', 5.2, shp);

  /* ---------- F15 拍照建场景：按识别配置参数化生成定制栏舍 ---------- */
  const buildCustomBarn = (cfg: PhotoSceneConfig): THREE.Group => {
    const g = new THREE.Group();
    const wM = mat(new THREE.Color(cfg.wall_color).getHex(), 0, 0.85);
    const rM = mat(new THREE.Color(cfg.roof_color).getHex(), 0.15, 0.55);
    const n = Math.min(6, Math.max(2, Math.round(cfg.pen_count || 4)));
    const sp = cfg.species;
    // 舍体外壳
    box(g, 12, 0.16, 8, 0, 0.08, 0, concM);
    box(g, 11.4, 2.6, 0.18, 0, 1.3, -3.7, wM);
    box(g, 0.18, 2.6, 7.4, -5.7, 1.3, 0, wM);
    box(g, 0.18, 2.6, 7.4, 5.7, 1.3, 0, wM);
    for (let x = -5.2; x <= 5.21; x += 2.6) box(g, 0.18, 2.6, 0.18, x, 1.3, 3.7, wM);
    box(g, 11.4, 0.3, 0.18, 0, 2.45, 3.7, wM);
    {
      const th = Math.atan(1.4 / 3.7), sl = Math.hypot(1.4, 3.7) + 0.6;
      const c1 = box(g, 12.3, 0.12, sl, 0, 3.3, 1.85, rM); c1.rotation.x = th; c1.userData.isRoof = true;
      const c2 = box(g, 12.3, 0.12, sl, 0, 3.3, -1.85, rM); c2.rotation.x = -th; c2.userData.isRoof = true;
      const c3 = box(g, 12.3, 0.1, 0.26, 0, 4.05, 0, rM); c3.userData.isRoof = true;
    }
    {
      const s = new THREE.Shape();
      s.moveTo(-3.7, 0); s.lineTo(3.7, 0); s.lineTo(0, 1.4); s.lineTo(-3.7, 0);
      const gg = new THREE.ExtrudeGeometry(s, { depth: 0.16, bevelEnabled: false });
      const g1 = new THREE.Mesh(gg, wM); g1.rotation.y = Math.PI / 2; g1.position.set(5.8, 2.6, 0); g.add(g1);
      const g2 = new THREE.Mesh(gg, wM); g2.rotation.y = -Math.PI / 2; g2.position.set(-5.8, 2.6, 0); g.add(g2);
    }
    // 栏位（n 栏 × 2 列）+ 中央走道 + 料线/饮水/粪沟
    const penW = 10.4 / n;
    for (const zc of [1.9, -1.9]) {
      const sg = Math.sign(zc);
      for (let i = 0; i < n; i++) {
        const xc = -5.2 + penW * (i + 0.5);
        box(g, penW - 0.12, 0.06, 2.3, xc, 0.19, zc, slatM);
        cyl(g, 0.035, 0.6, xc, 1.5, sg * 0.9, pipeM);
        cyl(g, 0.28, 0.12, xc, 0.28, sg * 0.9, feedY);
        box(g, 0.09, 0.18, 0.12, xc, 1.52, sg * 3.3, waterM);
      }
      box(g, 10.4, 0.05, 0.05, 0, 0.55, sg * 0.62, metalM);
      box(g, 10.4, 0.05, 0.05, 0, 1.0, sg * 0.62, metalM);
      for (let i = 0; i <= n; i++) {
        const xp = -5.2 + penW * i;
        box(g, 0.06, 1.05, 0.06, xp, 0.6, sg * 0.62, metalM);
        if (i < n) {
          box(g, 0.06, 0.05, 2.45, xp + penW / 2, 0.55, zc, metalM);
          box(g, 0.06, 0.05, 2.45, xp + penW / 2, 1.0, zc, metalM);
          box(g, 0.06, 1.1, 0.06, xp + penW / 2, 0.6, zc - 1.22, metalM);
          box(g, 0.06, 1.1, 0.06, xp + penW / 2, 0.6, zc + 1.22, metalM);
        }
      }
      cyl(g, 0.05, 10.6, 0, 1.85, sg * 0.9, pipeM, [0, 0, Math.PI / 2]);
      cyl(g, 0.04, 10.6, 0, 1.7, sg * 3.3, pipeM, [0, 0, Math.PI / 2]);
      box(g, 10.4, 0.14, 0.4, 0, 0.13, sg * 3.55, chanM);
      const sc = box(g, 0.5, 0.3, 0.36, 0, 0.28, sg * 3.55, darkM);
      sc.userData.isScraper = true;
      sc.userData.off = sg * 1.6;
      customAnim.scrapers.push(sc);
    }
    // 物种特征设施
    if (sp === 'pig') {
      if (cfg.has_fans) {
        for (const fx of [-3, 0, 3]) {
          cyl(g, 0.55, 0.16, fx, 1.4, -3.85, fanM, [Math.PI / 2, 0, 0]);
          const hub = new THREE.Group();
          hub.position.set(fx, 1.4, -3.9);
          hub.userData.isFanHub = true;
          hub.userData.fanAxis = 'z';
          const b1 = new THREE.Mesh(boxGeo, mat(0x9aa4ac, 0.6, 0.35)); b1.scale.set(0.72, 0.09, 0.04); hub.add(b1);
          const b2 = new THREE.Mesh(boxGeo, mat(0x9aa4ac, 0.6, 0.35)); b2.scale.set(0.09, 0.72, 0.04); hub.add(b2);
          g.add(hub);
          customAnim.fans.push(hub);
        }
      }
      if (cfg.has_cool_pad) box(g, 4, 1.7, 0.14, -2, 0.95, 3.85, padM);
      if (cfg.has_silo) {
        cyl(g, 0.9, 2.6, -2.2, 1.3, -5.1, siloM);
        cone(g, 0.95, 0.7, -2.2, 2.95, -5.1, siloM);
      }
      for (const [px, pz, ps] of [[-3.5, 1.5, 1], [2, -1.3, 1], [3.2, 1.8, 0.9], [-1.5, -2, 0.95]] as const) {
        const a = makePig(ps); a.position.set(px, 0.02, pz); a.rotation.y = Math.random() * 6.28; g.add(a);
      }
    } else if (sp === 'cattle') {
      box(g, 10.8, 0.4, 0.75, 0, 0.32, 3.1, mat(0xd8d3c8, 0, 0.85));
      box(g, 10.6, 0.08, 0.5, 0, 0.55, 3.1, feedG);
      for (let x = -5; x <= 5.01; x += 0.55) cyl(g, 0.032, 1.5, x, 0.75, 2.5, metalM);
      box(g, 10.8, 0.06, 0.06, 0, 1.42, 2.5, metalM);
      box(g, 10.8, 0.06, 5.2, 0, 0.19, -0.4, mat(0xc9b98a, 0, 0.95));
      for (const wx of [-3, 3]) {
        box(g, 1.8, 0.45, 0.65, wx, 0.28, -3.1, waterM);
        box(g, 1.7, 0.08, 0.55, wx, 0.5, -3.1, waterIn);
      }
      for (const [cx, cz, cr] of [[-2.8, 0.4, 0.4], [0.5, 1, 2.2], [2.8, -0.6, 4]] as const) {
        const c = makeCattle(); c.position.set(cx, 0.22, cz); c.rotation.y = cr; g.add(c);
      }
      if (cfg.has_yard) {
        box(g, 11, 0.06, 3, 0, 0.19, 6.3, mat(0xd9cba8, 0, 0.95));
        fenceLine(g, -5.5, 4.9, 5.5, 4.9, 0.16, metalM);
        fenceLine(g, -5.5, 4.9, -5.5, 7.8, 0.16, metalM);
        fenceLine(g, 5.5, 4.9, 5.5, 7.8, 0.16, metalM);
        fenceLine(g, -5.5, 7.8, 5.5, 7.8, 0.16, metalM);
        const c = makeCattle(); c.position.set(-1.5, 0.22, 6.2); c.rotation.y = 1.2; g.add(c);
      }
    } else {
      // 高床羊舍
      box(g, 11, 0.1, 7, 0, 0.05, 0, mat(0x8a857c, 0, 0.92));
      for (let x = -4.8; x <= 4.81; x += 1.9)
        for (const zc of [-2.8, 2.8]) box(g, 0.16, 1.3, 0.16, x, 0.65, zc, concM);
      for (let z = -3; z <= 3.01; z += 0.18) box(g, 10, 0.07, 0.09, 0, 1.3, z, woodM);
      box(g, 10, 0.08, 1, 0, 1.3, 3.6, concM);
      fenceLine(g, -5, -3, 5, -3, 1.37, metalM);
      fenceLine(g, -5, 2.9, 5, 2.9, 1.37, metalM);
      fenceLine(g, -5, -3, -5, 2.9, 1.37, metalM);
      fenceLine(g, 5, -3, 5, 2.9, 1.37, metalM);
      box(g, 9.6, 0.28, 0.45, 0, 1.51, 2.65, mat(0xcbb27e, 0, 0.8));
      box(g, 9.4, 0.06, 0.35, 0, 1.68, 2.65, feedG);
      for (const bx of [-3.8, 3.8]) cyl(g, 0.3, 0.8, bx, 1.77, -2.2, waterM);
      for (const [sx2, sz2] of [[-2.6, -0.8], [0.4, 1.1], [2.6, -1.5]] as const) {
        const s2 = makeSheep(1); s2.position.set(sx2, 1.37, sz2); s2.rotation.y = Math.random() * 6.28; g.add(s2);
      }
      if (cfg.has_yard) {
        box(g, 10.2, 0.06, 2.8, 0, 0.19, 6.2, mat(0xd9cba8, 0, 0.95));
        box(g, 10.2, 1.4, 0.15, 0, 0.7, 7.6, wM);
        box(g, 0.15, 1.4, 2.9, -5.05, 0.7, 6.2, wM);
        box(g, 0.15, 1.4, 2.9, 5.05, 0.7, 6.2, wM);
        box(g, 1.6, 0.5, 1, 2.6, 0.28, 6, concM);
        box(g, 1.4, 0.08, 0.8, 2.6, 0.48, 6, mat(0x4f7fa8, 0.05, 0.3));
        const s2 = makeSheep(0.95); s2.position.set(-1.4, 0.22, 6.1); g.add(s2);
      }
    }
    // 场区地坪 + 标签
    box(g, 22, 0.04, 4.4, 0, 0.02, 10.2, roadM);
    const label = createLabelSprite(cfg.barn_name || '我的场舍');
    label.position.set(0, 5.2, 0);
    g.add(label);
    g.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh) { m.castShadow = true; m.receiveShadow = true; }
    });
    return g;
  };
  buildCustom = (cfg) => {
    const gg = buildCustomBarn(cfg);
    scene.add(gg);
    return gg;
  };

  /* ---------- 阴影 ---------- */
  scene.traverse((o) => {
    const m = o as THREE.Mesh;
    if (m.isMesh) { m.castShadow = true; m.receiveShadow = true; }
  });
  ground.castShadow = false;

  /* ---------- 栏舍布局：一栋长舍 + 舍内双列栏位分区（真实育肥场制式，搜猪网/中国养猪网设计口径：
     一栋 10~15 间栏、双列式+中央走道、每栏 10~40 头全进全出；圈数多则并行加排） ---------- */
  const groupMap: Record<Species, THREE.Group> = { pig, cattle: cow, sheep: shp };
  const instanceGroups: THREE.Group[] = [];
  const clearInstances = () => {
    for (const g of instanceGroups) scene.remove(g);
    instanceGroups.length = 0;
    instAnim.fans.length = 0;
    instAnim.scrapers.length = 0;
  };
  const hideTemplates = () => { for (const k of ALL_SPECIES) groupMap[k].visible = false; };

  // 长舍构建：sections 为该舍内的栏（名称+存栏），双列布置、每栏独立标签与动物
  const buildPenBarn = (
    sp: Species,
    sections: BarnInstance[],
    rowIndex: number,
    anchorsOut: [number, number][],
  ): THREE.Group => {
    const g = new THREE.Group();
    const wM = mat(0xf4f1e8, 0, 0.85);
    const rM = mat(0x8f9aa3, 0.15, 0.55);
    wM.userData.heatable = true;   // 红温联动：圈舍高温时墙体/屋面泛红脉冲（本舍局部材质）
    rM.userData.heatable = true;
    const isSheep = sp === 'sheep';
    const isCattle = sp === 'cattle';
    const floorY = isSheep ? 1.37 : 0;   // 羊舍为高床
    // 栏位规格随存栏数缩放（育肥畜约 1~1.5㎡/只口径）：30 头 → 开间 7.2m、舍深 9.2m
    const maxCnt = Math.max(0, ...sections.map((s) => s.count ?? 0));
    const depth = Math.min(9.4, Math.max(7.4, 7.4 + maxCnt * 0.06));
    const dep2 = depth / 2;
    const bayW = Math.min(8.0, Math.max(3.0, 3.0 + maxCnt * 0.14));
    const bays = Math.ceil(sections.length / 2);
    const len = bays * bayW + 2.0;
    const half = len / 2;
    const penZH = (dep2 - 1.3) / 2;      // 单列栏位进深的一半（中央走道 ±0.65）
    const zFront = (isCattle ? 0.9 : 1.85) * (penZH / 1.2);
    const zBack = -zFront;
    g.position.z = -rowIndex * ROW_GAP;

    // 地坪（羊舍为高床：床下集粪区 + 支柱）
    box(g, len + 1, 0.16, depth + 1, 0, 0.08, 0, concM);
    if (isSheep) {
      box(g, len, 0.1, depth - 0.6, 0, 0.05, 0, mat(0x8a857c, 0, 0.92));
      for (let x = -half + 1; x <= half - 1.01; x += 2.0)
        for (const zc of [-(dep2 - 0.8), dep2 - 0.8]) box(g, 0.16, 1.3, 0.16, x, 0.65, zc, concM);
    }
    // 围护结构：后墙 + 山墙×2 + 前立柱 + 前上梁
    box(g, len, 2.6, 0.18, 0, floorY + 1.3, -dep2 + 0.09, wM);
    box(g, 0.18, 2.6, depth, -half + 0.09, floorY + 1.3, 0, wM);
    box(g, 0.18, 2.6, depth, half - 0.09, floorY + 1.3, 0, wM);
    for (let x = -half + 1.4; x <= half - 1.41; x += 2.6)
      box(g, 0.18, 2.6, 0.18, x, floorY + 1.3, dep2 - 0.09, wM);
    box(g, len, 0.3, 0.18, 0, floorY + 2.45, dep2 - 0.09, wM);
    // 双坡屋面 + 屋脊
    {
      const th = Math.atan(1.4 / dep2);
      const sl = Math.hypot(1.4, dep2) + 0.5;
      const r1 = box(g, len + 1.2, 0.12, sl, 0, floorY + 3.3, dep2 / 2, rM); r1.rotation.x = th; r1.userData.isRoof = true;
      const r2 = box(g, len + 1.2, 0.12, sl, 0, floorY + 3.3, -dep2 / 2, rM); r2.rotation.x = -th; r2.userData.isRoof = true;
      const r3 = box(g, len + 1.2, 0.1, 0.26, 0, floorY + 4.05, 0, rM); r3.userData.isRoof = true;
    }
    // 山墙封板
    {
      const s = new THREE.Shape();
      s.moveTo(-dep2, 0); s.lineTo(dep2, 0); s.lineTo(0, 1.4); s.lineTo(-dep2, 0);
      const gg = new THREE.ExtrudeGeometry(s, { depth: 0.16, bevelEnabled: false });
      const g1 = new THREE.Mesh(gg, wM); g1.rotation.y = Math.PI / 2; g1.position.set(half, floorY + 2.6, 0); g.add(g1);
      const g2 = new THREE.Mesh(gg, wM); g2.rotation.y = -Math.PI / 2; g2.position.set(-half, floorY + 2.6, 0); g.add(g2);
    }
    // 中央走道两侧通长栅栏
    for (const zc2 of [0.62, -0.62]) {
      box(g, len - 1.6, 0.05, 0.05, 0, floorY + 0.55, zc2, metalM);
      box(g, len - 1.6, 0.05, 0.05, 0, floorY + 1.0, zc2, metalM);
    }
    // 跨间分隔栅（前后两段，中央走道贯通）
    for (let b = 0; b <= bays; b++) {
      const fxB = -half + 1.0 + b * bayW;
      for (const [z1, z2] of [[0.65, dep2 - 0.65], [-(dep2 - 0.65), -0.65]] as const) {
        const zc3 = (z1 + z2) / 2;
        box(g, 0.06, 0.05, z2 - z1, fxB, floorY + 0.55, zc3, metalM);
        box(g, 0.06, 0.05, z2 - z1, fxB, floorY + 1.0, zc3, metalM);
        box(g, 0.06, 1.05, 0.06, fxB, floorY + 0.53, z1 + 0.03, metalM);
        box(g, 0.06, 1.05, 0.06, fxB, floorY + 0.53, z2 - 0.03, metalM);
      }
    }
    // 通长料线（猪/羊）+ 靠墙饮水管线
    if (!isCattle) {
      for (const zc2 of [0.9, -0.9]) cyl(g, 0.05, len - 1.8, 0, floorY + 1.85, zc2, pipeM, [0, 0, Math.PI / 2]);
    }
    for (const zc2 of [dep2 - 0.5, -(dep2 - 0.5)]) cyl(g, 0.04, len - 1.8, 0, floorY + 1.7, zc2, pipeM, [0, 0, Math.PI / 2]);
    // 清粪：沿外墙通长粪沟 + 刮板（猪/牛）
    if (!isSheep) {
      for (const zc2 of [dep2 - 0.35, -(dep2 - 0.35)]) {
        box(g, len - 1.6, 0.14, 0.4, 0, 0.13, zc2, chanM);
        const sc = box(g, 0.6, 0.3, 0.36, 0, 0.28, zc2, darkM);
        sc.userData.isScraper = true;
        sc.userData.off = zc2 > 0 ? 0 : Math.PI;
        sc.userData.amp = Math.max(3, half - 1.5);
      }
    }
    if (sp === 'pig') {
      // 负压风机（后墙均布）+ 湿帘（前端外墙）+ 料塔×2（西端）
      for (let fx = -half + 2.5; fx <= half - 2.4; fx += 5.0) {
        cyl(g, 0.55, 0.16, fx, 1.4, -dep2 + 0.18, fanM, [Math.PI / 2, 0, 0]);
        const hub = new THREE.Group();
        hub.position.set(fx, 1.4, -dep2 + 0.14);
        hub.userData.isFanHub = true; hub.userData.fanAxis = 'z';
        const b1 = new THREE.Mesh(boxGeo, mat(0x9aa4ac, 0.6, 0.35)); b1.scale.set(0.72, 0.09, 0.04); hub.add(b1);
        const b2 = new THREE.Mesh(boxGeo, mat(0x9aa4ac, 0.6, 0.35)); b2.scale.set(0.09, 0.72, 0.04); hub.add(b2);
        g.add(hub);
        instAnim.fans.push(hub);
      }
      box(g, 3.2, 1.7, 0.14, half - 2.4, 0.95, dep2 + 0.12, padM);
      for (const sx of [-(half - 1.6), -(half - 3.8)]) {
        cyl(g, 0.9, 2.6, sx, 1.3, -dep2 - 1.3, siloM);
        cone(g, 0.95, 0.7, sx, 2.95, -dep2 - 1.3, siloM);
      }
    }
    if (isCattle) {
      // TMR 通长饲槽 + 颈枷 + 分点饮水槽
      box(g, len - 1.6, 0.4, 0.75, 0, 0.32, dep2 - 0.65, mat(0xd8d3c8, 0, 0.85));
      box(g, len - 1.8, 0.08, 0.5, 0, 0.55, dep2 - 0.65, feedG);
      for (let x = -half + 1.2; x <= half - 1.21; x += 0.55) cyl(g, 0.032, 1.5, x, 0.75, dep2 - 1.3, metalM);
      box(g, len - 1.6, 0.06, 0.06, 0, 1.42, dep2 - 1.3, metalM);
      for (let b = 0; b < bays; b += 2) {
        const wx = -half + 1.0 + b * bayW + bayW / 2;
        box(g, 1.8, 0.45, 0.65, wx, 0.28, -dep2 + 0.55, waterM);
        box(g, 1.7, 0.08, 0.55, wx, 0.5, -dep2 + 0.55, waterIn);
      }
    }
    if (isSheep) {
      // 高床漏缝板条（通长）+ 高架饲喂走道 + 草架料槽 + 运动场 + 上床斜坡
      for (let z = -dep2 + 0.5; z <= dep2 - 0.49; z += 0.18) box(g, len - 1.0, 0.07, 0.09, 0, 1.3, z, woodM);
      box(g, len - 1.2, 0.08, 1.0, 0, 1.3, dep2 + 0.35, concM);
      for (let x = -half + 1; x <= half - 1.01; x += 2.0) box(g, 0.16, 1.3, 0.16, x, 0.65, dep2 + 0.35, concM);
      box(g, len - 1.6, 0.28, 0.45, 0, floorY + 0.21, dep2 - 0.55, mat(0xcbb27e, 0, 0.8));
      box(g, len - 1.8, 0.06, 0.35, 0, floorY + 0.38, dep2 - 0.55, feedG);
      box(g, len - 1.6, 0.5, 0.06, 0, floorY + 0.85, dep2 - 0.35, woodM).rotation.x = 0.45;
      for (let b = 0; b < bays; b++) {
        const wx = -half + 1.0 + b * bayW + bayW / 2;
        box(g, 0.22, 0.15, 0.18, wx, floorY + 0.15, -dep2 + 0.35, waterM);
      }
      for (const bx of [half - 0.6, half - 1.5]) cyl(g, 0.3, 0.8, bx, floorY + 0.43, -2.2, waterM);
      fenceLine(g, -half + 0.8, -(dep2 - 0.6), half - 0.8, -(dep2 - 0.6), floorY + 0.07, metalM);
      fenceLine(g, -half + 0.8, dep2 - 0.6, half - 0.8, dep2 - 0.6, floorY + 0.07, metalM);
      fenceLine(g, -half + 0.8, -(dep2 - 0.6), -half + 0.8, dep2 - 0.6, floorY + 0.07, metalM);
      fenceLine(g, half - 0.8, -(dep2 - 0.6), half - 0.8, dep2 - 0.6, floorY + 0.07, metalM);
      box(g, len, 0.06, 3.0, 0, 0.19, dep2 + 2.2, mat(0xd9cba8, 0, 0.95));
      box(g, len, 1.4, 0.15, 0, 0.7, dep2 + 3.65, wM);
      box(g, 0.15, 1.4, 3.0, -half + 0.4, 0.7, dep2 + 2.2, wM);
      box(g, 0.15, 1.4, 3.0, half - 0.4, 0.7, dep2 + 2.2, wM);
      box(g, 1.0, 0.08, 3.0, half - 0.6, 0.67, dep2 + 0.9, concM).rotation.x = 0.43;
    }
    // 各栏：漏缝地面/垫料 + 喂料落料口 + 动物（按存栏数）+ 标签
    sections.forEach((sec, i) => {
      const col = i % 2;
      const bay = Math.floor(i / 2);
      const px = -half + 1.0 + bay * bayW + bayW / 2;
      const pz = col === 0 ? zFront : zBack;
      anchorsOut.push([px, pz]);
      if (isSheep) { /* 高床板条已通铺 */ }
      else box(g, bayW - 0.15, 0.06, penZH * 2 - 0.1, px, 0.19, pz, isCattle ? mat(0xc9b98a, 0, 0.95) : slatM);
      if (!isCattle) {
        const dz = pz > 0 ? 0.9 : -0.9;
        cyl(g, 0.035, 0.7, px, floorY + 1.5, dz, pipeM);
        cyl(g, 0.26, 0.12, px, floorY + 0.28, dz, feedY);
      }
      const cnt = Math.min(12, sec.count ?? 0);
      const y0 = isSheep ? floorY + 0.02 : isCattle ? 0.22 : 0.24;
      const colDx = bayW / 5;
      const rowDz = Math.max(0.7, penZH - 0.35);
      for (let i2 = 0; i2 < cnt; i2++) {
        const s2 = 0.9 + ((i2 * 37) % 18) / 100;
        const dx = ((i2 % 4) - 1.5) * colDx + (((i2 * 53) % 20) / 100 - 0.1);
        const dz2 = (Math.floor(i2 / 4) - 1) * rowDz + (((i2 * 97) % 20) / 100 - 0.1);
        const a = isSheep ? makeSheep(s2) : isCattle ? makeCattle() : makePig(s2);
        a.position.set(px + dx, y0, pz + dz2);
        a.rotation.y = ((i2 * 73) % 360) * Math.PI / 180;
        g.add(a);
      }
      const label = createLabelSprite((sec.name || DEFAULT_LABEL[sp]) + (sec.count ? ` · ${sec.count}头` : ''));
      label.userData.isBarnLabel = true;
      label.position.set(px, floorY + 4.7, pz);
      g.add(label);
    });
    g.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh) { m.castShadow = true; m.receiveShadow = true; }
    });
    return g;
  };

  // 收集该舍的可发热材质（墙体/屋面），红温脉冲只影响本舍
  const collectHeatMats = (g: THREE.Group): THREE.MeshStandardMaterial[] => {
    const set = new Set<THREE.Material>();
    g.traverse((o) => {
      const m = o as THREE.Mesh;
      const mm = m.material as THREE.MeshStandardMaterial | undefined;
      if (m.isMesh && mm && mm.userData?.heatable) set.add(mm);
    });
    return [...set] as THREE.MeshStandardMaterial[];
  };

  const layoutPens = (list: BarnInstance[]): void => {
    clearInstances();
    hideTemplates();
    const items = (list.length > 0 ? list : [{ name: '', species: displaySpecies.value, count: 0 }])
      .slice(0, 12);
    const sp = items[0].species;
    const rows: BarnInstance[][] = [];
    for (let i = 0; i < items.length; i += PER_BARN) rows.push(items.slice(i, i + PER_BARN));
    const anchors: [number, number][] = [];
    const hotNames = props.hotPens ?? [];
    let len = 0;
    rows.forEach((row, r) => {
      const before = anchors.length;
      const g = buildPenBarn(sp, row, r, anchors);
      // 恢复用户拖动摆放的偏移（默认位：x=0，z=-r*ROW_GAP）
      const key = row.map((s) => s.name || `row${r}`).sort().join('|');
      const saved = savedLayout[key];
      rowPos[r] = { x: saved?.dx ?? 0, z: -r * ROW_GAP + (saved?.dz ?? 0) };
      rowKeys[r] = key;
      g.position.set(rowPos[r].x, 0, rowPos[r].z);
      scene.add(g);
      instanceGroups.push(g);
      instHeat[r] = { mats: collectHeatMats(g), hot: row.some((s) => hotNames.includes(s.name)) };
      for (let i2 = before; i2 < anchors.length; i2++) len = Math.max(len, Math.abs(anchors[i2][0]));
    });
    barnsBox = rows.map((row, r) => ({ x: rowPos[r].x, z: rowPos[r].z, half: len + 0.6, row: r, species: sp }));
    instMeta = items.map((it, i2) => ({
      off: anchors[i2]?.[0] ?? 0, species: it.species, name: it.name, row: Math.floor(i2 / PER_BARN),
    }));
    const k = rows.length - 1;
    goal.pos.set(0, 11 + len * 0.32 + k * 2, 20 + len * 0.8 + k * 6);
    goal.tgt.set(0, 1.5, -k * (ROW_GAP / 2));
    goal.active = true;
    pensAnchor = anchors;
    roofGhost(ghostOn); // 重建后恢复虚化状态（防数据重取冲掉）
  };
  applyLayout = (list) => layoutPens(list);
  hideAll = () => { hideTemplates(); clearInstances(); };

  /* ---------- 拖动布置模式：圈舍长舍 + 场区设施均可按住拖到新位置，偏移落 localStorage ---------- */
  const dragRay = new THREE.Raycaster();
  const dragNdc = new THREE.Vector2();
  const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
  const hitPoint = new THREE.Vector3();
  let dragging: {
    g: THREE.Group;
    kind: 'row' | 'site';
    row?: number;
    fac?: { g: THREE.Group; name: string; zones: string[] };
    dx: number; dz: number;
  } | null = null;
  const DRAG_LIMIT = { x: 62, zMin: -46, zMax: 36 }; // 场区地面范围内拖动
  const dragTargets = (): THREE.Object3D[] => [
    ...instanceGroups,
    ...siteFacilities.map((f) => f.g),
  ];
  const barnRootOf = (o: THREE.Object3D | null): { kind: 'row' | 'site'; g: THREE.Group; row?: number; fac?: typeof siteFacilities[number] } | null => {
    while (o) {
      const rowIdx = instanceGroups.indexOf(o as THREE.Group);
      if (rowIdx >= 0) return { kind: 'row', g: instanceGroups[rowIdx], row: rowIdx };
      const f = siteFacilities.find((sf) => sf.g === o);
      if (f) return { kind: 'site', g: f.g, fac: f };
      o = o.parent;
    }
    return null;
  };
  toggleLayoutMode = (on: boolean): void => {
    controls.enableRotate = !on;
    controls.enablePan = !on;
    if (on) {
      controls.autoRotate = false;
      autoRotate.value = false;
    }
    renderer.domElement.style.cursor = on ? 'grab' : '';
    if (!on) dragging = null;
  };
  resetBarnLayout = (): void => {
    try { localStorage.removeItem(LS_LAYOUT); } catch { /* 存储不可用时忽略 */ }
    savedLayout = {};
    for (const f of siteFacilities) {
      f.g.position.set(0, 0, 0);
      shiftZones(f.zones, 0, 0);
    }
    if (photoMode.value) syncPhoto();
    else applyLayout(props.barns ?? []);
  };
  const onDragDown = (e: PointerEvent): void => {
    if (!layoutMode.value || e.button !== 0) return;
    const rect = renderer.domElement.getBoundingClientRect();
    dragNdc.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    dragNdc.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    dragRay.setFromCamera(dragNdc, camera);
    const hits = dragRay.intersectObjects(dragTargets(), true);
    if (hits.length === 0) return;
    const root = barnRootOf(hits[0].object);
    if (!root) return;
    dragging = {
      g: root.g, kind: root.kind, row: root.row, fac: root.fac,
      dx: hits[0].point.x - root.g.position.x, dz: hits[0].point.z - root.g.position.z,
    };
    renderer.domElement.style.cursor = 'grabbing';
    e.preventDefault();
  };
  const onDragMove = (e: PointerEvent): void => {
    if (!dragging) return;
    const rect = renderer.domElement.getBoundingClientRect();
    dragNdc.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    dragNdc.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    dragRay.setFromCamera(dragNdc, camera);
    if (!dragRay.ray.intersectPlane(groundPlane, hitPoint)) return;
    const dg = dragging;
    const nx = Math.max(-DRAG_LIMIT.x, Math.min(DRAG_LIMIT.x, hitPoint.x - dg.dx));
    const nz = Math.max(DRAG_LIMIT.zMin, Math.min(DRAG_LIMIT.zMax, hitPoint.z - dg.dz));
    if (dg.kind === 'row') {
      dg.g.position.set(nx, 0, nz);
      rowPos[dg.row!] = { x: nx, z: nz };
      const bb = barnsBox.find((b) => b.row === dg.row);
      if (bb) { bb.x = nx; bb.z = nz; }
    } else if (dg.fac) {
      dg.g.position.set(nx, 0, nz);
      shiftZones(dg.fac.zones, nx, nz); // 点击识别感应区跟随
    }
  };
  const onDragUp = (): void => {
    if (!dragging) return;
    const dg = dragging;
    if (dg.kind === 'row') {
      const rp = rowPos[dg.row!];
      const key = rowKeys[dg.row!];
      if (rp && key) {
        savedLayout[key] = { dx: rp.x, dz: rp.z - (-dg.row! * ROW_GAP) };
        try { localStorage.setItem(LS_LAYOUT, JSON.stringify(savedLayout)); } catch { /* 存储不可用时忽略 */ }
      }
    } else if (dg.fac) {
      savedLayout[`site:${dg.fac.name}`] = { dx: dg.g.position.x, dz: dg.g.position.z };
      try { localStorage.setItem(LS_LAYOUT, JSON.stringify(savedLayout)); } catch { /* 存储不可用时忽略 */ }
    }
    dragging = null;
    renderer.domElement.style.cursor = layoutMode.value ? 'grab' : '';
    syncFocus(); // 告警定位标记跟随新位置
  };
  renderer.domElement.addEventListener('pointerdown', onDragDown);
  renderer.domElement.addEventListener('pointermove', onDragMove);
  renderer.domElement.addEventListener('pointerup', onDragUp);
  renderer.domElement.addEventListener('pointerleave', onDragUp);

  // 屋顶虚化开关（场景内注册）：模板/场区棚顶共用材质 + 各长舍局部屋顶按 isRoof 标记
  roofGhost = (on: boolean) => {
    const apply = (m: THREE.Material | undefined) => {
      if (!m) return;
      const std = m as THREE.MeshStandardMaterial;
      std.transparent = on;
      std.opacity = on ? 0.22 : 1;
      std.depthWrite = !on;
      std.needsUpdate = true;
    };
    const rm = roofMat;
    if (rm) apply(rm);
    scene.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh && o.userData.isRoof) apply(m.material as THREE.Material);
    });
  };
  layoutPens(props.barns ?? []);

  // 告警定位标记（红色脉冲环 + 光球，focus 时显示于锚点）
  const alertMarker = new THREE.Group();
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.55, 0.7, 36),
    new THREE.MeshBasicMaterial({ color: 0xff3b30, transparent: true, opacity: 0.85, side: THREE.DoubleSide }),
  );
  ring.rotation.x = -Math.PI / 2;
  const ball = new THREE.Mesh(
    new THREE.SphereGeometry(0.15, 12, 10),
    new THREE.MeshBasicMaterial({ color: 0xff453a }),
  );
  ball.position.y = 0.5;
  alertMarker.add(ring);
  alertMarker.add(ball);
  alertMarker.visible = false;
  scene.add(alertMarker);
  focusMarker = alertMarker;

  /* ---------- 渲染循环 ---------- */
  const clock = new THREE.Clock();
  let raf = 0;
  const animate = () => {
    raf = requestAnimationFrame(animate);
    const dt = Math.min(clock.getDelta(), 0.1);
    const t = clock.elapsedTime;
    pigScraperA.position.x = 4.5 * Math.sin(t * 0.5);   // 清粪刮板往复
    pigScraperB.position.x = 4.5 * Math.sin(t * 0.5 + Math.PI);
    cowScraper.position.x = 4.5 * Math.sin(t * 0.4);
    sheepScraper.position.x = 4.4 * Math.sin(t * 0.45);
    for (const fb of fanBlades) fb.rotation.z += 0.22;   // 负压风机叶轮
    for (const cf of ceilFanHubs) cf.rotation.y += 0.3;  // 牛舍吊扇
    for (const sf of instAnim.scrapers)                  // 多栋实例：刮板往复
      sf.position.x = (sf.userData.amp ?? 4.5) * Math.sin(t * 0.5 + (sf.userData.off ?? 0));
    for (const f2 of instAnim.fans) {                    // 多栋实例：风机旋转
      if (f2.userData.fanAxis === 'y') f2.rotation.y += 0.3;
      else f2.rotation.z += 0.22;
    }
    for (const cs of customAnim.scrapers)                // 定制场景：刮板往复 + 风机旋转
      cs.position.x = 4 * Math.sin(t * 0.45 + (cs.userData.off ?? 0));
    for (const cf2 of customAnim.fans) cf2.rotation.z += 0.22;
    const mk = focusMarker;
    if (mk && mk.visible) mk.scale.setScalar(1 + 0.16 * Math.sin(t * 5)); // 脉冲
    // 天气渐变：天色/雾/光照向当前天气模式平滑过渡；雨天粒子下落循环
    skyColor.lerp(
      tmpSky.setHex(weatherTarget.sky),
      Math.min(1, dt * 3),
    );
    fog.color.copy(skyColor);
    fog.near += (weatherTarget.fogNear - fog.near) * Math.min(1, dt * 3);
    keyLight.intensity += (weatherTarget.keyInt - keyLight.intensity) * Math.min(1, dt * 3);
    hemiLight.intensity += (weatherTarget.hemiInt - hemiLight.intensity) * Math.min(1, dt * 3);
    if (rain.visible) {
      const fall = 26 * dt;
      const drift = 2.2 * dt;
      for (let i = 0; i < RAIN_COUNT; i++) {
        const k = i * 6;
        rainPos[k] += drift; rainPos[k + 3] += drift;
        rainPos[k + 1] -= fall; rainPos[k + 4] -= fall;
        if (rainPos[k + 4] < 0) resetDrop(i);
      }
      (rainGeo.attributes.position as THREE.BufferAttribute).needsUpdate = true;
    }
    // 红温联动：高温圈舍墙体/屋面泛红脉冲，复常后熄灭
    for (const h of instHeat) {
      for (const m of h.mats) {
        if (h.hot) {
          m.emissive.setHex(0xff3517);
          m.emissiveIntensity = 0.3 + 0.18 * Math.sin(t * 3.2);
        } else if (m.emissiveIntensity !== 0) {
          m.emissiveIntensity = 0;
        }
      }
    }
    if (goal.active) {                                   // 切舍/定位相机平滑过渡
      camera.position.lerp(goal.pos, 0.06);
      controls.target.lerp(goal.tgt, 0.06);
      if (
        camera.position.distanceTo(goal.pos) < 0.05 &&
        controls.target.distanceTo(goal.tgt) < 0.05
      ) goal.active = false;
    }
    controls.update();
    renderer.render(scene, camera);
  };
  animate();

  /* ---------- 点击拾取设施名称（射线命中 → 最近命名感应区） ---------- */
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  let downX = 0, downY = 0;
  const onPointerDown = (e: PointerEvent) => { downX = e.clientX; downY = e.clientY; };
  const onClickPick = (e: MouseEvent) => {
    if (layoutMode.value) return; // 布置模式下点击=选中拖动，不弹设施卡片
    if (Math.hypot(e.clientX - downX, e.clientY - downY) > 6) return; // 拖拽视角不触发
    const rect = renderer.domElement.getBoundingClientRect();
    ndc.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    ndc.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(ndc, camera);
    const hits = raycaster.intersectObjects(scene.children, true);
    if (hits.length === 0) return;
    const hit = hits[0];
    if ((hit.object as THREE.Sprite).isSprite) return; // 标签牌本身不参与拾取
    const p = hit.point;
    let bestN = ''; let bestScore = Infinity; let bestSub = ''; let barnSub = ''; let inBarn = false;
    const consider = (n: string, x: number, y: number, z: number, r: number, sub = '') => {
      const d = Math.hypot(p.x - x, p.y - y, p.z - z);
      if (d < r && d / r < bestScore) { bestScore = d / r; bestN = n; bestSub = sub; }
    };
    for (const z of SITE_ZONES) consider(z.n, z.p[0], z.p[1], z.p[2], z.r);
    // 栏舍设施：先定位命中的长舍，再按舍内局部坐标匹配（多排 z 偏移已计入）
    for (const bb of barnsBox) {
      if (Math.abs(p.z - bb.z) > 4.3 || Math.abs(p.x - bb.x) > bb.half + 0.6) continue;
      inBarn = true;
      const sections = instMeta.filter((m) => m.row === bb.row);
      let nearest = sections[0];
      for (const m of sections) {
        if (nearest && Math.abs(p.x - m.off) < Math.abs(p.x - nearest.off)) nearest = m;
      }
      barnSub = nearest ? (nearest.name || DEFAULT_LABEL[nearest.species]) : '';
      for (const z of BARN_ZONES[bb.species] ?? []) consider(z.n, z.p[0], z.p[1], z.p[2], z.r, barnSub);
    }
    let name = bestN; let sub = bestSub;
    if (!name) { // 兜底：栏舍主体（含所属圈名）/ 场区地面
      if (inBarn) { name = '栏舍主体'; sub = barnSub; }
      else name = '场区地面';
    }
    picked.value = { name, sub, x: e.clientX - rect.left, y: e.clientY - rect.top };
  };
  renderer.domElement.addEventListener('pointerdown', onPointerDown);
  renderer.domElement.addEventListener('click', onClickPick);

  const onResize = () => {
    if (!mount.clientWidth || !mount.clientHeight) return;
    camera.aspect = mount.clientWidth / mount.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(mount.clientWidth, mount.clientHeight);
  };
  window.addEventListener('resize', onResize);

  disposeScene = () => {
    cancelAnimationFrame(raf);
    window.removeEventListener('resize', onResize);
    renderer.domElement.removeEventListener('pointerdown', onPointerDown);
    renderer.domElement.removeEventListener('click', onClickPick);
    renderer.domElement.removeEventListener('pointerdown', onDragDown);
    renderer.domElement.removeEventListener('pointermove', onDragMove);
    renderer.domElement.removeEventListener('pointerup', onDragUp);
    renderer.domElement.removeEventListener('pointerleave', onDragUp);
    controls.dispose();
    if (orbitControls === controls) orbitControls = null;
    renderer.dispose();
    scene.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh) {
        m.geometry.dispose();
        (m.material as THREE.Material).dispose();
      }
    });
    renderer.domElement.remove();
  };

  // 初始同步：先定位（若有），再拍照定制（若有），顺序不可颠倒
  syncFocus();
  syncPhoto();

  watch(() => props.barns, () => {
    if (photoMode.value) return; // 拍照定制模式下不切模板
    applyLayout(props.barns ?? []);
  });
  watch(
    [() => props.focus?.species, () => props.focus?.hotspot, () => props.focus?.penName],
    () => syncFocus(),
  );
  watch(() => props.photoConfig, () => syncPhoto());
  watch(() => props.weather, (m) => applyWeather(m));
  watch(() => props.hotPens, (hot) => applyHotFlags(hot ?? []));
});

/** 按高温圈舍名单刷新各排热标记（不重建场景） */
function applyHotFlags(names: string[]): void {
  // instHeat 与排对应，但没有排→圈名映射；用 instMeta 圈名→行反查
  for (const h of instHeat) h.hot = false;
  for (const m of instMeta) {
    if (!names.includes(m.name)) continue;
    const h = instHeat[m.row];
    if (h) h.hot = true;
  }
}

// 点击提示卡 5 秒自动消失（再次点击即时刷新内容并重置计时）
watch(picked, (v) => {
  if (!v) return;
  window.clearTimeout(pickedTimer);
  pickedTimer = window.setTimeout(() => { picked.value = null; }, 5000);
});

onBeforeUnmount(() => {
  window.clearTimeout(pickedTimer);
  disposeScene?.();
  disposeScene = null;
});
</script>

<template>
  <div class="relative w-full h-full">
    <div ref="mountEl" class="absolute inset-0" />

    <!-- 工具条 -->
    <div class="absolute left-4 top-4 z-10 flex gap-2">
      <button
        type="button"
        @click="toggleRotate"
        class="px-3.5 py-1.5 rounded-lg text-[13px] border transition-colors"
        :class="autoRotate
          ? 'bg-primary text-white border-primary'
          : 'bg-white/90 text-text-primary border-border hover:bg-white'"
      >
        自动环绕：{{ autoRotate ? '开' : '关' }}
      </button>
      <button
        type="button"
        @click="resetView"
        class="px-3.5 py-1.5 rounded-lg text-[13px] border border-border bg-white/90 text-text-primary hover:bg-white transition-colors"
      >
        复位视角
      </button>
      <button
        type="button"
        @click="enterLayout"
        class="px-3.5 py-1.5 rounded-lg text-[13px] border transition-colors"
        :class="layoutMode
          ? 'bg-[#e03e36] text-white border-[#e03e36]'
          : 'bg-white/90 text-text-primary border-border hover:bg-white'"
      >
        {{ layoutMode ? '完成布置' : '布置模式' }}
      </button>
      <button
        v-if="layoutMode"
        type="button"
        @click="resetLayout"
        class="px-3.5 py-1.5 rounded-lg text-[13px] border border-border bg-white/90 text-text-secondary hover:bg-white transition-colors"
      >
        恢复默认布局
      </button>
    </div>

    <!-- 设备图例（跟随当前养殖物种） -->
    <div class="absolute left-4 bottom-4 z-10 max-w-[460px] rounded-xl border border-border bg-white/85 backdrop-blur px-3.5 py-2.5 text-[12px] leading-[1.9] text-text-secondary">
      <div>
        <span class="inline-block w-2 h-2 rounded-full mr-1.5 align-middle" :style="{ background: legend.color }" />
        <b class="text-text-primary">{{ legend.title }}</b>：{{ legend.desc }}
      </div>
      <div class="border-t border-border mt-1 pt-1 text-text-tertiary">{{ SITE_LEGEND }}</div>
    </div>

    <!-- 点击设施名称提示卡（跟随点击位置，5 秒自动消失） -->
    <div v-if="picked" class="absolute z-30 pointer-events-none" :style="pickedStyle">
      <div class="rounded-lg border border-primary-300 bg-white/95 px-3 py-1.5 shadow-lg">
        <p class="text-[13px] font-semibold text-text-primary leading-tight">📍 {{ picked.name }}</p>
        <p v-if="picked.sub" class="text-[11px] text-text-tertiary leading-tight mt-0.5">{{ picked.sub }}</p>
      </div>
    </div>

    <!-- 操作提示 -->
    <div class="absolute right-4 bottom-4 z-10 text-[12px] text-text-tertiary">
      {{ layoutMode ? '布置模式：按住圈舍/场区设施拖到新位置 · 滚轮缩放 · 点「完成布置」保存' : '左键拖拽旋转 · 滚轮缩放 · 右键平移' }}
    </div>
  </div>
</template>
