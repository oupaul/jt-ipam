<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NDataTable,
  NSpace,
  NIcon,
  NInput,
  NSelect,
  NButton,
  NTag,
  NPopover,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import { listAudit, verifyAuditChain, type AuditLog } from "@/api/admin";
import { AuditIcon, RefreshIcon, AdminIcon as VerifyIcon } from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useRouter } from "vue-router";
const { t } = useI18n();

const router = useRouter();

// 把 audit (object_type, object_id) → 可點連結
function renderObjectLink(objectType: string | null, objectId: string | null) {
  if (!objectId) return "—";
  const short = objectId.slice(0, 8) + "…";
  const linkStyle = "color: var(--primary-color, #18a058); text-decoration: none; cursor: pointer;";
  const go = (name: string, params?: any, query?: any) =>
    h("a", {
      href: "#", style: linkStyle,
      onClick: (e: MouseEvent) => { e.preventDefault(); router.push({ name, params, query }); },
    }, short);
  switch (objectType) {
    case "section":      return go("section-detail", { id: objectId });
    case "subnet":       return go("subnet-detail", { id: objectId });
    case "device":       return go("device-detail", { id: objectId });
    case "user":         return go("users");
    case "group":        return go("groups");
    case "customer":     return go("customers");
    case "nat":          return go("nat");
    case "vlan":         return go("vlans");
    case "vrf":          return go("vrfs");
    case "ip_address":
    case "ip":           return go("addresses", undefined, { q: short });
    case "scan_agent":   return go("scan_agents");
    case "webhook":      return go("webhooks");
    case "custom_field": return go("custom_fields");
    case "ip_request":   return go("requests");
    default:             return short;
  }
}

const { visibleKeys: auditVis, setVisible: auditSet, reset: auditReset } = useColumnPrefs(
  "audit",
  ["id", "ts", "actor", "actor_ip", "object_type", "object_link", "action", "diff", "this_hash_hex"],
  ["id", "ts", "actor", "actor_ip", "object_type", "object_link", "action", "diff", "this_hash_hex"],
);
const auditPickerItems = [
  { key: "id", label: "ID" },
  { key: "ts", label: t("cols.time") },
  { key: "actor", label: t("cols.actor") },
  { key: "actor_ip", label: "IP" },
  { key: "object_type", label: t("cols.object_type") },
  { key: "object_link", label: t("cols.target") },
  { key: "action", label: t("cols.action") },
  { key: "diff", label: t("cols.diff") },
  { key: "this_hash_hex", label: t("cols.hash") },
];

const msg = useMessage();
const rows = ref<AuditLog[]>([]);
const total = ref(0);
const loading = ref(false);
const verifying = ref(false);
const filterObjType = ref<string | null>(null);

// 常見的 object_type 值 (與 backend 寫 audit 時用的名字對齊)
const objTypeOptions = [
  "user", "group", "section", "subnet", "ip_address", "device", "rack", "location",
  "vlan", "vlan_domain", "vrf", "nat",
  "auth", "api_token", "anomaly",
  "dns_server", "librenms_instance", "opnsense_firewall", "opnsense_alias_mapping",
  "wazuh_instance", "scan_agent", "webhook",
  "phpipam_migration", "ip_request", "custom_field",
].map((v) => ({ label: v, value: v }));
const filterAction = ref("");
const limit = ref(50);
const offset = ref(0);

const allColumns = computed<DataTableColumns<AuditLog>>(() => autoSort([
  { title: t("audit.id"), key: "id", width: 70 },
  {
    title: t("audit.ts"), key: "ts", width: 180,
    render: (r) => fmtDateTime(r.ts),
  },
  {
    title: t("audit.actor"), key: "actor", width: 130,
    render: (r) => r.actor_user_id ? `${r.actor_user_id.slice(0, 8)}…` : "(system)",
  },
  { title: "IP", key: "actor_ip", width: 130, render: (r) => r.actor_ip ?? "—" },
  {
    title: t("audit.object_type"), key: "object_type", width: 150,
    render: (r) => h_tag(r.object_type),
  },
  {
    title: t("cols.target"), key: "object_link", width: 140,
    render: (r) => renderObjectLink(r.object_type, r.object_id),
  },
  {
    title: t("audit.action"), key: "action", width: 120,
    render: (r) => h_tag(r.action, action_color(r.action)),
  },
  {
    title: t("audit.diff"), key: "diff", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => r.diff
      ? renderDiffPopover(r.diff)
      : "—",
  },
  {
    title: t("audit.this_hash"), key: "this_hash_hex", width: 120,
    render: (r) => `${r.this_hash_hex.slice(0, 10)}…`,
  },
]));

const columns = computed<DataTableColumns<AuditLog>>(() =>
  allColumns.value.filter((c: any) => auditVis.value.includes(c.key)),
);

async function refresh() {
  loading.value = true;
  try {
    const res = await listAudit({
      object_type: filterObjType.value || undefined,
      action: filterAction.value || undefined,
      limit: limit.value, offset: offset.value,
    });
    rows.value = res.items;
    total.value = res.total;
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

async function verify() {
  verifying.value = true;
  try {
    const res = await verifyAuditChain();
    if (res.ok) {
      msg.success(t("audit.chain_ok", { n: res.checked }));
    } else {
      msg.error(t("audit.chain_broken", { id: String(res.broken_at_id) }));
    }
  } catch {
    msg.error(t("errors.network"));
  } finally {
    verifying.value = false;
  }
}

import { h, defineComponent } from "vue";

function action_color(action: string): "default" | "success" | "warning" | "error" | "info" {
  if (action.includes("login_success")) return "success";
  if (action.includes("login_failed") || action === "delete") return "error";
  if (action === "create") return "info";
  if (action === "update" || action === "sync") return "warning";
  return "default";
}

function h_tag(text: string, type: "default" | "success" | "warning" | "error" | "info" = "default") {
  return h(NTag, { type, size: "small", bordered: false }, () => text);
}

function renderDiffPopover(diff: Record<string, unknown>) {
  const summary = JSON.stringify(diff).slice(0, 60);
  return h(
    NPopover,
    { trigger: "hover", style: { maxWidth: "480px" } },
    {
      trigger: () => h("code", { style: "font-size: 12px; cursor: help" }, summary + "…"),
      default: () =>
        h("pre", { style: "white-space: pre-wrap; max-height: 400px; overflow: auto" },
          JSON.stringify(diff, null, 2)),
    },
  );
}

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><AuditIcon /></n-icon>
        <span>{{ t("audit.title") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px" align="center">
      <n-select v-model:value="filterObjType" :options="objTypeOptions" filterable clearable
                :placeholder="t('audit.filter_object_type')"
                @update:value="refresh"
                style="width: 240px" />
      <n-input v-model:value="filterAction" :placeholder="t('audit.filter_action')"
               style="width: 220px" clearable />
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" :loading="verifying" @click="verify">
        <template #icon><n-icon><VerifyIcon /></n-icon></template>
        {{ t("audit.verify_chain") }}
      </n-button>
      <ColumnPicker :all="auditPickerItems" :visible="auditVis"
                    @update:visible="auditSet" @reset="auditReset" />
      <span style="opacity: 0.6">total: {{ total }}</span>
    </n-space>
    <n-data-table
      :columns="columns" :data="rows" :loading="loading"
      :pagination="{
        page: Math.floor(offset / limit) + 1,
        pageSize: limit,
        itemCount: total,
        onUpdatePage: (p) => { offset = (p - 1) * limit; void refresh(); },
      }"
      remote :bordered="false" :scroll-x="1240"
    >
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>
  </n-card>
</template>
