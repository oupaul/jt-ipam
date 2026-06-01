<script setup lang="ts">
/**
 * 網路拓樸圖 — Cytoscape.js + cose-bilkent layout。
 *
 * Phase 3 MVP：
 *  - 節點 = device，依 type 顏色編碼
 *  - 邊 = cable / wireless / vpn，三種樣式可區分
 *  - 點節點顯示資訊
 *  - 切換 wireless / vpn 顯示
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NSpace,
  NCheckbox,
  NSpin,
  NButton,
  NText,
  NSelect,
  useMessage,
} from "naive-ui";
import { NIcon } from "naive-ui";
import { TopologyIcon, RefreshIcon } from "@/icons";
import { useRouter } from "vue-router";
import cytoscape from "cytoscape";
import coseBilkent from "cytoscape-cose-bilkent";
import { getTopology, type TopologyData } from "@/api/topology";
import { listSubnets } from "@/api/subnets";

cytoscape.use(coseBilkent as any);

const { t } = useI18n();
const msg = useMessage();
const containerRef = ref<HTMLDivElement | null>(null);
const includeWireless = ref(true);
const includeVpn = ref(true);
const includeL3 = ref(true);
const loading = ref(false);
const selected = ref<Record<string, any> | null>(null);

// 友善欄位名稱（中文），對應 cytoscape node/edge data 的 key
const FIELD_LABELS = computed<Record<string, string>>(() => ({
  label: t("cols.name"),
  type: t("topology.field_type"),
  vendor: t("topology.field_vendor"),
  model: t("topology.field_model"),
  kind: t("topology.field_kind"),
  b_endpoint: t("topology.field_b_endpoint"),
  ip: "IP",
  mac: "MAC",
  serial: t("topology.field_serial"),
  rack: t("topology.field_rack"),
  location: t("topology.field_location"),
  os: t("topology.field_os"),
  hardware: t("topology.field_hardware"),
  sw_version: t("topology.field_sw_version"),
  sysname: t("topology.field_sysname"),
  status: t("topology.field_status"),
  description: t("topology.field_description"),
  via: t("topology.field_via"),
}));
const VIA_LABELS = computed<Record<string, string>>(() => ({
  ip: t("topology.via_ip"),
  name: t("topology.via_name"),
  arp: t("topology.via_arp"),
}));
const TYPE_LABELS = computed<Record<string, string>>(() => ({
  router: t("topology.type_router"),
  switch: t("topology.type_switch"),
  firewall: t("topology.type_firewall"),
  ap: t("topology.type_ap"),
  server: t("topology.type_server"),
  storage: t("topology.type_storage"),
  ipmi: "IPMI",
  other: t("topology.type_other"),
  subnet: t("topology.type_subnet"),
  vpn_site: t("topology.type_vpn_site"),
}));
const KIND_LABELS = computed<Record<string, string>>(() => ({
  cable: t("topology.kind_cable"),
  wireless: t("topology.kind_wireless"),
  vpn: t("topology.kind_vpn"),
  l3: t("topology.kind_l3"),
}));
// 內部欄位不顯示給使用者看
const HIDDEN_FIELDS = new Set([
  "id", "source", "target", "a_device_id", "b_device_id",
  "rack_id", "location_id", "subnet_uuid", "label",
]);

function displayValue(key: string, val: any): string {
  if (key === "type") return TYPE_LABELS.value[val] ?? String(val);
  if (key === "kind") return KIND_LABELS.value[val] ?? String(val);
  if (key === "via") {
    return String(val).split(",").map((v) => VIA_LABELS.value[v] ?? v).join("、");
  }
  if (key === "status") {
    if (val === "up") return t("topology.status_up");
    if (val === "down") return t("topology.status_down");
  }
  return String(val);
}

const selectedRows = computed(() => {
  const d = selected.value;
  if (!d) return [];
  return Object.keys(d)
    .filter((k) => !HIDDEN_FIELDS.has(k) && d[k] != null && d[k] !== "")
    .map((k) => ({ key: k, label: FIELD_LABELS.value[k] ?? k, value: displayValue(k, d[k]) }));
});
const selectedTitle = computed(() =>
  selected.value ? (selected.value.label ?? selected.value.id ?? t("topology.element")) : "",
);
const router = useRouter();
// 是 device 節點（非 subnet:/vpnsite: 合成節點）→ 可連到裝置頁
const selectedDeviceId = computed<string | null>(() => {
  const d = selected.value;
  if (!d || typeof d.id !== "string") return null;
  if (d.id.includes(":")) return null;            // subnet:/vpnsite:
  if (d.type === "subnet" || d.type === "vpn_site") return null;
  return d.id;
});
const selectedSubnetId = computed<string | null>(() => {
  const d = selected.value;
  return d && d.type === "subnet" && d.subnet_uuid ? String(d.subnet_uuid) : null;
});
function goDevice() {
  if (selectedDeviceId.value) router.push({ name: "device-detail", params: { id: selectedDeviceId.value } });
}
function goSubnet() {
  if (selectedSubnetId.value) router.push({ name: "subnet-detail", params: { id: selectedSubnetId.value } });
}
const subnetIds = ref<string[]>([]);
const subnetOptions = ref<{ label: string; value: string }[]>([]);

async function loadSubnetOptions() {
  try {
    const r = await listSubnets({ page: 1, pageSize: 500 });
    subnetOptions.value = r.items.map((s) => ({
      label: s.description ? `${s.cidr} — ${s.description}` : s.cidr,
      value: s.id,
    }));
  } catch { /* silent */ }
}

let cy: cytoscape.Core | null = null;

const NODE_COLOURS: Record<string, string> = {
  router: "#6366f1",
  switch: "#22c55e",
  firewall: "#ef4444",
  ap: "#3b82f6",
  server: "#6b7280",
  storage: "#f59e0b",
  ipmi: "#ec4899",
  other: "#9ca3af",
  subnet: "#0ea5e9",  // L3 subnet 節點 — 青藍色，跟 device 區分
  vpn_site: "#9333ea",  // site-to-site VPN 遠端站點 — 紫色（跟 vpn 邊一致）
};

async function refresh() {
  loading.value = true;
  try {
    const data = await getTopology({
      includeWireless: includeWireless.value,
      includeVpn: includeVpn.value,
      includeL3: includeL3.value,
      subnetIds: subnetIds.value,
    });
    render(data);
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

function render(data: TopologyData) {
  if (!containerRef.value) return;
  if (cy) {
    cy.destroy();
    cy = null;
  }
  cy = cytoscape({
    container: containerRef.value,
    elements: [...data.nodes, ...data.edges],
    style: [
      {
        selector: "node",
        style: {
          "background-color": ((node: any) =>
            NODE_COLOURS[node.data("type") as string] || NODE_COLOURS.other) as any,
          label: "data(label)",
          color: "#fff",
          "text-outline-color": "#0f172a",
          "text-outline-width": 1,
          "font-size": 11,
          "text-valign": "center",
          "text-halign": "center",
          width: 38,
          height: 38,
        },
      },
      {
        selector: "edge",
        style: {
          width: 2,
          "curve-style": "bezier",
          "line-color": "#94a3b8",
          "target-arrow-shape": "none",
        },
      },
      {
        selector: 'edge[kind = "cable"]',
        style: {
          "line-color": "#475569",
          width: 2,
        },
      },
      {
        selector: 'edge[kind = "wireless"]',
        style: {
          "line-color": "#3b82f6",
          "line-style": "dashed",
        },
      },
      {
        selector: 'edge[kind = "vpn"]',
        style: {
          "line-color": "#9333ea",
          "line-style": "dashed",
          "line-dash-pattern": [10, 5],
          width: 4,
          label: "data(label)",
          "font-size": 10,
          "font-weight": "bold",
          color: "#7e22ce",
          "text-background-color": "#ffffff",
          "text-background-opacity": 0.9,
          "text-background-padding": "3px",
          "text-rotation": "autorotate",
        },
      },
      {
        selector: 'edge[kind = "l3"]',
        style: {
          "line-color": "#0ea5e9",
          "line-style": "dashed",
          width: 1.5,
          opacity: 0.7,
        },
      },
      {
        selector: 'node[type = "subnet"]',
        style: {
          shape: "round-rectangle",
          width: 70,
          height: 28,
          "font-size": 10,
          "font-weight": "bold",
        },
      },
      {
        selector: 'node[type = "vpn_site"]',
        style: {
          shape: "diamond",
          width: 44,
          height: 44,
          "font-size": 10,
        },
      },
      {
        selector: ":selected",
        style: {
          "border-width": 3,
          "border-color": "#fbbf24",
        },
      },
    ],
    layout: {
      name: "cose-bilkent",
      idealEdgeLength: 90,
      nodeRepulsion: 4500,
      edgeElasticity: 0.45,
      animate: false,
    } as any,
  });

  cy.on("tap", "node", (evt) => {
    selected.value = { ...evt.target.data() };
  });
  cy.on("tap", "edge", (evt) => {
    selected.value = { ...evt.target.data() };
  });
  cy.on("tap", (evt) => {
    if (evt.target === cy) {
      selected.value = null;
    }
  });
}

watch(subnetIds, () => { void refresh(); });
watch([includeWireless, includeVpn, includeL3], () => {
  void refresh();
});

onMounted(() => { void loadSubnetOptions(); void refresh(); });
onUnmounted(() => {
  if (cy) cy.destroy();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><TopologyIcon /></n-icon>
        <span>{{ t("nav.topology") }}</span>
      </n-space>
    </template>
    <template #header-extra>
      <n-space align="center">
        <n-select
          v-model:value="subnetIds"
          :options="subnetOptions"
          multiple filterable clearable
          :placeholder="t('topology.filter_subnets')"
          style="min-width: 220px; max-width: 380px"
          :max-tag-count="2"
        />
        <n-checkbox v-model:checked="includeWireless">{{ t("topology.wireless") }}</n-checkbox>
        <n-checkbox v-model:checked="includeVpn">{{ t("topology.vpn") }}</n-checkbox>
        <n-checkbox v-model:checked="includeL3">{{ t("topology.l3") }}</n-checkbox>
        <n-button size="small" @click="refresh">
          <template #icon><n-icon><RefreshIcon /></n-icon></template>
          {{ t("common.refresh") }}
        </n-button>
      </n-space>
    </template>
    <n-spin :show="loading">
      <div class="topology-shell">
        <div ref="containerRef" class="cy"></div>
        <n-card v-if="selected" size="small" class="info-pane" :title="selectedTitle" closable @close="selected = null">
          <table class="info-table">
            <tbody>
              <tr v-for="row in selectedRows" :key="row.key">
                <th>{{ row.label }}</th>
                <td>{{ row.value }}</td>
              </tr>
            </tbody>
          </table>
          <template v-if="selectedDeviceId || selectedSubnetId" #action>
            <n-button v-if="selectedDeviceId" size="small" type="primary" ghost block @click="goDevice">
              {{ t("topology.open_device") }}
            </n-button>
            <n-button v-else size="small" type="primary" ghost block @click="goSubnet">
              {{ t("topology.open_subnet") }}
            </n-button>
          </template>
        </n-card>
      </div>
    </n-spin>
    <div class="topo-legend">
      <span class="lg lg-head">{{ t("topology.legend_links") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#475569" stroke-width="2"/></svg>{{ t("topology.kind_cable") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#3b82f6" stroke-width="2" stroke-dasharray="5,3"/></svg>{{ t("topology.kind_wireless") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#9333ea" stroke-width="4" stroke-dasharray="8,4"/></svg>{{ t("topology.kind_vpn") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#0ea5e9" stroke-width="1.5" stroke-dasharray="5,3"/></svg>{{ t("topology.kind_l3") }}</span>
      <span class="lg lg-sep"></span>
      <span class="lg lg-head">{{ t("topology.legend_nodes") }}</span>
      <span class="lg"><i class="dot" style="background:#ef4444"></i>{{ t("topology.type_firewall") }}</span>
      <span class="lg"><i class="dot" style="background:#6366f1"></i>{{ t("topology.type_router") }}</span>
      <span class="lg"><i class="dot" style="background:#22c55e"></i>{{ t("topology.type_switch") }}</span>
      <span class="lg"><i class="dot" style="background:#3b82f6"></i>AP</span>
      <span class="lg"><i class="dot" style="background:#9ca3af"></i>{{ t("topology.server_other") }}</span>
      <span class="lg"><svg width="14" height="14"><rect x="2" y="2" width="9" height="9" transform="rotate(45 7 7)" fill="#9333ea"/></svg>{{ t("topology.type_vpn_site") }}</span>
      <span class="lg"><i class="dot dot-rect" style="background:#0ea5e9"></i>{{ t("topology.type_subnet") }}</span>
      <span class="lg muted">{{ t("topology.click_hint") }}</span>
    </div>
  </n-card>
</template>

<style scoped>
.topo-legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  margin-top: 8px;
  font-size: 12px;
  opacity: 0.85;
}
.topo-legend .lg { display: inline-flex; align-items: center; gap: 6px; }
.topo-legend .lg.muted { opacity: 0.6; margin-left: auto; }
.topo-legend .lg-head { font-weight: 600; opacity: 0.55; }
.topo-legend .lg-sep { width: 1px; height: 14px; background: rgba(127,127,127,0.3); }
.topo-legend .dot { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
.topo-legend .dot-rect { width: 16px; height: 9px; border-radius: 3px; }
.info-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.info-table th {
  text-align: left;
  white-space: nowrap;
  padding: 3px 10px 3px 0;
  color: var(--n-text-color-3, #888);
  font-weight: 500;
  vertical-align: top;
}
.info-table td { padding: 3px 0; word-break: break-all; }
.topology-shell {
  position: relative;
  width: 100%;
  /* 自動延展到接近視窗底部（扣掉頂列／卡片頭／工具列／圖例的概略高度） */
  height: calc(100vh - 270px);
  min-height: 420px;
  background: rgba(127, 127, 127, 0.04);
  border-radius: 6px;
}
.cy {
  width: 100%;
  height: 100%;
}
.info-pane {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 320px;
  max-height: 60vh;
  overflow: auto;
  z-index: 10;
}
.info-pane pre {
  font-size: 11px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
