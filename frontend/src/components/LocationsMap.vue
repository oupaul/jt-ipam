<script setup lang="ts">
/**
 * 機房 / 地點世界地圖 — 完全自帶、零外部相依。
 *
 * 不嵌入 OpenStreetMap 圖磚：改用內建的 Natural Earth 110m 陸地輪廓（public domain，
 * 已預先投影成 SVG path），以等距圓柱（equirectangular）投影把有經緯度的地點標成標記，
 * 並自動依所有點選出剛好塞得下的視窗。**不對外發任何請求** → 隔離網路可用、不洩漏管理員
 * 正在看哪些站點、CSP 不需放行外部網域、可用最強的 COEP require-corp。
 */
import { computed, onMounted, onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { WORLD_LAND_PATH } from "@/assets/world-land";

interface Pt { id: string; name: string; lat: number; lng: number; }
const props = defineProps<{ points: Pt[] }>();
const emit = defineEmits<{ (e: "select", id: string): void }>();
const { t } = useI18n();

const boxRef = ref<HTMLDivElement | null>(null);
const boxW = ref(800);
const boxH = 340;

// 等距圓柱投影：經度 lng→x(0..360)、緯度 lat→y(0..180)
const ex = (lng: number) => lng + 180;
const ey = (lat: number) => 90 - lat;

const valid = computed(() => props.points.filter((p) =>
  Number.isFinite(p.lat) && Number.isFinite(p.lng) && (p.lat !== 0 || p.lng !== 0)));

// 依所有點算出 viewBox（含留白、對齊容器長寬比、夾在世界範圍內）
const view = computed(() => {
  const pts = valid.value;
  const W = boxW.value, H = boxH;
  if (!pts.length) return null;
  const xs = pts.map((p) => ex(p.lng)), ys = pts.map((p) => ey(p.lat));
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  // 至少給一個合理跨度（單點不會爆縮），再加 1.4x 留白
  let spanX = Math.max(Math.max(...xs) - Math.min(...xs), 24) * 1.4;
  let spanY = Math.max(Math.max(...ys) - Math.min(...ys), 16) * 1.4;
  const boxAspect = W / H;
  if (spanX / spanY < boxAspect) spanX = spanY * boxAspect;
  else spanY = spanX / boxAspect;
  const vw = Math.min(spanX, 360), vh = Math.min(spanY, 180);
  const vx0 = Math.min(Math.max(cx - vw / 2, 0), 360 - vw);
  const vy0 = Math.min(Math.max(cy - vh / 2, 0), 180 - vh);
  return { vx0, vy0, vw, vh, W, H };
});

const viewBox = computed(() => {
  const v = view.value;
  return v ? `${v.vx0} ${v.vy0} ${v.vw} ${v.vh}` : "0 0 360 180";
});

const markers = computed(() => {
  const v = view.value;
  if (!v) return [];
  return valid.value.map((p) => ({
    id: p.id, name: p.name,
    left: (ex(p.lng) - v.vx0) / v.vw * v.W,
    top: (ey(p.lat) - v.vy0) / v.vh * v.H,
  }));
});

let ro: ResizeObserver | null = null;
onMounted(() => {
  if (boxRef.value) {
    boxW.value = boxRef.value.clientWidth || 800;
    ro = new ResizeObserver(() => { if (boxRef.value) boxW.value = boxRef.value.clientWidth || 800; });
    ro.observe(boxRef.value);
  }
});
onBeforeUnmount(() => { ro?.disconnect(); });
</script>

<template>
  <div v-if="valid.length" ref="boxRef" class="lmap" :style="{ height: boxH + 'px' }">
    <svg class="lmap-svg" :viewBox="viewBox" preserveAspectRatio="none" :width="boxW" :height="boxH">
      <path :d="WORLD_LAND_PATH" class="lmap-land" />
    </svg>
    <div
      v-for="m in markers" :key="m.id" class="lmap-pin"
      :style="{ left: m.left + 'px', top: m.top + 'px' }"
      :title="m.name" @click="emit('select', m.id)"
    >
      <span class="lmap-dot"></span>
      <span class="lmap-name">{{ m.name }}</span>
    </div>
    <div class="lmap-attr">Natural Earth</div>
    <div class="lmap-hint">{{ t("locations.map_all_hint") }}</div>
  </div>
</template>

<style scoped>
.lmap {
  position: relative;
  width: 100%;
  overflow: hidden;
  border: 1px solid var(--n-border-color, #ddd);
  border-radius: 8px;
  background: #adcee8;            /* 海洋 */
}
.lmap-svg { position: absolute; left: 0; top: 0; display: block; }
.lmap-land { fill: #e6e3d7; stroke: #b9b29a; stroke-width: 0.15; vector-effect: non-scaling-stroke; }
html[data-theme="dark"] .lmap { background: #0b1a2b; }
html[data-theme="dark"] .lmap-land { fill: #243447; stroke: #3a4d63; }
html[data-theme="dark"] .lmap-attr { background: rgba(15,24,37,.7); color: #aab8cc; }
html[data-theme="dark"] .lmap-hint { background: rgba(15,24,37,.75); color: #cdd8e6; }
.lmap-pin {
  position: absolute;
  transform: translate(-50%, -100%);
  display: flex; flex-direction: column; align-items: center;
  cursor: pointer; z-index: 5;
}
.lmap-dot {
  width: 14px; height: 14px; border-radius: 50% 50% 50% 0;
  background: #e74c3c; border: 2px solid #fff; transform: rotate(-45deg);
  box-shadow: 0 1px 3px rgba(0,0,0,0.5);
}
.lmap-name {
  margin-top: 2px; font-size: 11px; font-weight: 600; color: #1f2937;
  background: rgba(255,255,255,0.85); padding: 0 4px; border-radius: 3px;
  white-space: nowrap; max-width: 160px; overflow: hidden; text-overflow: ellipsis;
}
.lmap-pin:hover .lmap-dot { background: #18a058; }
.lmap-attr {
  position: absolute; right: 4px; bottom: 2px; z-index: 6;
  font-size: 10px; color: #555; background: rgba(255,255,255,0.7); padding: 0 4px; border-radius: 3px;
}
.lmap-hint {
  position: absolute; left: 6px; top: 6px; z-index: 6;
  font-size: 11px; color: #444; background: rgba(255,255,255,0.75); padding: 1px 6px; border-radius: 4px;
}
</style>
