<script setup lang="ts">
/**
 * 機櫃 U 位視覺化 (phpIPAM 招牌功能)。
 *
 * 比 phpIPAM 改進：
 *  - 顏色按 device type 區分 (router/switch/firewall/server/...)
 *  - 越界 / 重疊衝突明顯標示
 *  - 點 device 跳詳情
 *  - U 編號從上到下標示，符合機房現場認知
 */
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { NCard, NTag, NEmpty, NAlert, NSpace, NTooltip } from "naive-ui";
import type { RackDiagram } from "@/api/racks";

const { t } = useI18n();
const router = useRouter();
function goDevice(id: string) {
  router.push({ name: "device-detail", params: { id } });
}

interface Props {
  diagram: RackDiagram | null;
}
const props = defineProps<Props>();
const hoveredId = ref<string | null>(null);   // hover 某 U → 整台裝置點亮+框線

interface Cell {
  u: number;          // 1-based, top-most U
  device: {
    id: string;
    name: string;
    type: string;
    vendor: string | null;
    model: string | null;
    u_size: number;
    is_top: boolean;     // device 最上格
    is_bottom: boolean;  // device 最下格
    is_mid: boolean;     // device 垂直中間格（顯示名字 → 跨多 U 置中）
    primary_ip: string | null;
  } | null;
}

const cells = computed<Cell[]>(() => {
  if (!props.diagram) return [];
  const u_height = props.diagram.u_height;
  const bottomUp = props.diagram.numbering === "bottom-up";
  const map: Record<number, Cell> = {};
  for (let u = 1; u <= u_height; u++) map[u] = { u, device: null };
  for (const d of props.diagram.devices) {
    for (let u = d.u_position; u < d.u_position + d.u_size; u++) {
      if (map[u]) {
        map[u] = {
          u,
          device: {
            id: d.device_id, name: d.name, type: d.type,
            vendor: d.vendor, model: d.model, u_size: d.u_size,
            is_top: false, is_bottom: false, is_mid: false,
            primary_ip: d.primary_ip,
          },
        };
      }
    }
  }
  // 顯示順序：top-down → 高 U 在上（u_height..1）；bottom-up → U1 在上（1..u_height）
  const order: Cell[] = bottomUp
    ? Array.from({ length: u_height }, (_, i) => map[i + 1])
    : Array.from({ length: u_height }, (_, i) => map[u_height - i]);
  // 依「顯示上的鄰居」標出每台裝置的最上/最下/中間格（兩種編號方向皆正確）
  const runs: Record<string, number[]> = {};
  order.forEach((c, i) => {
    if (!c.device) return;
    const prev = order[i - 1]?.device?.id;
    const next = order[i + 1]?.device?.id;
    c.device.is_top = prev !== c.device.id;      // 顯示上最上格 → 畫上框
    c.device.is_bottom = next !== c.device.id;   // 顯示上最下格 → 畫下框
    (runs[c.device.id] ??= []).push(i);
  });
  for (const idxs of Object.values(runs)) {
    order[idxs[Math.floor((idxs.length - 1) / 2)]].device!.is_mid = true;
  }
  return order;
});

function colorFor(type: string): string {
  switch (type) {
    case "router":
      return "rgba(99, 102, 241, 0.85)"; // indigo
    case "switch":
      return "rgba(34, 197, 94, 0.85)";  // green
    case "firewall":
      return "rgba(239, 68, 68, 0.85)";  // red
    case "ap":
      return "rgba(59, 130, 246, 0.85)"; // blue
    case "server":
      return "rgba(107, 114, 128, 0.85)"; // grey
    case "storage":
      return "rgba(245, 158, 11, 0.85)"; // amber
    case "ipmi":
      return "rgba(236, 72, 153, 0.6)";  // pink
    default:
      return "rgba(107, 114, 128, 0.6)";
  }
}
</script>

<template>
  <n-card v-if="diagram" :title="`Rack: ${diagram.name} (${diagram.u_height}U)`">
    <n-space vertical :size="12">
      <n-alert
        v-if="diagram.conflicts.length > 0"
        type="warning"
        :title="`${diagram.conflicts.length} conflict(s)`"
      >
        <pre style="font-size: 11px; margin: 0">{{ JSON.stringify(diagram.conflicts, null, 2) }}</pre>
      </n-alert>

      <n-empty
        v-if="!diagram.devices.length"
        :description="t('rack_diagram.empty')"
      />

      <div v-else class="rack-wrap">
        <!-- U 編號：機櫃框外左側 gutter -->
        <div class="u-gutter">
          <div v-for="cell in cells" :key="'g' + cell.u" class="u-num-out">{{ cell.u }}</div>
        </div>
        <div class="rack-frame">
          <template v-for="cell in cells" :key="cell.u">
            <!-- 有裝置：hover 即時彈出結構化資訊 -->
            <n-tooltip v-if="cell.device" trigger="hover" :delay="60" placement="right">
              <template #trigger>
                <div
                  class="u-row u-occupied"
                  :class="{ 'u-top': cell.device.is_top, 'u-bottom': cell.device.is_bottom, 'u-hl': hoveredId === cell.device.id }"
                  :style="{ background: colorFor(cell.device.type) }"
                  @mouseenter="hoveredId = cell.device.id"
                  @mouseleave="hoveredId = null"
                  @click="goDevice(cell.device.id)"
                >
                  <template v-if="cell.device.is_mid">
                    <span class="d-name">{{ cell.device.name }}</span>
                    <n-tag size="tiny" :bordered="false" style="margin-left: 6px">
                      {{ cell.device.type }}
                    </n-tag>
                    <span v-if="cell.device.primary_ip" class="d-ip">{{ cell.device.primary_ip }}</span>
                  </template>
                </div>
              </template>
              <div class="rack-tip">
                <div class="rt-name">{{ cell.device.name }}</div>
                <div class="rt-row"><span>{{ t("cols.type") }}</span><b>{{ cell.device.type }}</b></div>
                <div v-if="cell.device.vendor" class="rt-row"><span>{{ t("cols.vendor") }}</span><b>{{ cell.device.vendor }}</b></div>
                <div v-if="cell.device.model" class="rt-row"><span>{{ t("cols.model") }}</span><b>{{ cell.device.model }}</b></div>
                <div v-if="cell.device.primary_ip" class="rt-row"><span>IP</span><b>{{ cell.device.primary_ip }}</b></div>
                <div class="rt-row"><span>{{ t("rack_diagram.height") }}</span><b>{{ cell.device.u_size }}U</b></div>
              </div>
            </n-tooltip>
            <!-- 空位 -->
            <div v-else class="u-row" :title="`Empty (U${cell.u})`"></div>
          </template>
        </div>
      </div>

      <div class="legend">
        <span class="legend-item" :style="{ background: colorFor('router') }">router</span>
        <span class="legend-item" :style="{ background: colorFor('switch') }">switch</span>
        <span class="legend-item" :style="{ background: colorFor('firewall') }">firewall</span>
        <span class="legend-item" :style="{ background: colorFor('server') }">server</span>
        <span class="legend-item" :style="{ background: colorFor('storage') }">storage</span>
        <span class="legend-item" :style="{ background: colorFor('ap') }">ap</span>
        <span class="legend-item" :style="{ background: colorFor('ipmi') }">ipmi</span>
      </div>
    </n-space>
  </n-card>
</template>

<style scoped>
/* 實體 19" rack 比例：寬 19" × 每 U 高 1.75" → 每 U 寬高比 ≈ 10.86 : 1。
   用 width 280px / U-row 28px 接近真實機櫃外觀 (18U 1:1.8、42U 1:4.2)。 */
.rack-wrap { display: flex; align-items: flex-start; gap: 6px; }
.u-gutter { display: flex; flex-direction: column; padding-top: 6px; flex: 0 0 auto; }
.u-num-out {
  position: relative;
  height: 28px;
  line-height: 28px;
  width: 26px;
  text-align: right;
  padding-right: 4px;
  font: bold 12px ui-monospace, SFMono-Regular, Menlo, monospace;
  color: rgba(127, 127, 127, 0.75);
}
/* 從數字拉一條短線對到該 U 列，方便對位 */
.u-num-out::after {
  content: "";
  position: absolute;
  right: -6px;
  top: 50%;
  width: 6px;
  height: 1px;
  background: rgba(127, 127, 127, 0.5);
}
.rack-frame {
  border: 2px solid rgba(127, 127, 127, 0.5);
  border-radius: 4px;
  padding: 4px;
  width: 250px;
  background: rgba(127, 127, 127, 0.04);
}
.u-row {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  height: 28px;
  border-bottom: 1px dashed rgba(127, 127, 127, 0.2);
  padding: 0 8px;
  font-size: 12px;
  font-family: monospace;
  color: white;
  position: relative;
}
.u-row:last-child {
  border-bottom: none;
}
.u-row:not(.u-occupied) {
  color: rgba(127, 127, 127, 0.5);
  background: transparent;
}
/* 裝置外框：每台（含多 U）都框起來，多 U 之間不畫內線 → 一眼看出佔幾 U */
.u-occupied {
  border-bottom: none;
  border-left: 2px solid rgba(0, 0, 0, 0.32);
  border-right: 2px solid rgba(0, 0, 0, 0.32);
  cursor: pointer;
}
.u-occupied.u-top { border-top: 2px solid rgba(0, 0, 0, 0.32); }
.u-occupied.u-bottom { border-bottom: 2px solid rgba(0, 0, 0, 0.32); }
/* hover 任一 U → 整台裝置點亮 + 框線（左右框；最上/最下格補上下框） */
.u-row.u-hl {
  filter: brightness(1.18) saturate(1.25);
  box-shadow: inset 2px 0 0 #fbbf24, inset -2px 0 0 #fbbf24;
  z-index: 1;
}
.u-row.u-hl.u-top { box-shadow: inset 2px 0 0 #fbbf24, inset -2px 0 0 #fbbf24, inset 0 2px 0 #fbbf24; }
.u-row.u-hl.u-bottom { box-shadow: inset 2px 0 0 #fbbf24, inset -2px 0 0 #fbbf24, inset 0 -2px 0 #fbbf24; }
.u-row.u-hl.u-top.u-bottom { box-shadow: inset 0 0 0 2px #fbbf24; }
.u-num {
  display: inline-block;
  width: 22px;
  text-align: right;
  margin-right: 6px;
  opacity: 0.8;
  font-weight: bold;
  flex-shrink: 0;
}
.d-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 110px;
}
.d-ip {
  margin-left: auto;
  font-size: 11px;
  opacity: 0.85;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 90px;
}
.rack-tip { font-size: 12px; line-height: 1.6; min-width: 150px; }
.rack-tip .rt-name { font-weight: 700; margin-bottom: 4px; font-size: 13px; }
.rack-tip .rt-row { display: flex; justify-content: space-between; gap: 16px; }
.rack-tip .rt-row > span { opacity: 0.65; }
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 11px;
}
.legend-item {
  padding: 2px 8px;
  border-radius: 3px;
  color: white;
  font-family: monospace;
}
</style>
