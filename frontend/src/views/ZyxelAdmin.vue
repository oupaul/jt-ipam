<script setup lang="ts">
/**
 * Zyxel 防火牆整合（Beta，實驗性）—— 與 FortiGate/pfSense/OPNsense 各自獨立設定。
 * Standalone ZLD 機種沒有 REST API，走 SSH CLI 唯讀拉取（只下 show 類指令，不會更動
 * Zyxel 上任何設定）。指令格式取自官方文件、無實機驗證，「測試連線」回逐指令的原始
 * 輸出片段，方便對照實際輸出跟預期格式是否一致。
 */
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip, NAlert, NModal, NForm,
  NFormItem, NInput, NInputNumber, NSwitch, NSelect, NCheckbox, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { listSubnets } from "@/api/subnets";
import {
  listZyxel, createZyxel, updateZyxel, deleteZyxel,
  testZyxel, syncZyxel,
  type ZyxelFirewall, type ZyxelDiagnosis,
} from "@/api/zyxel";
import {
  FirewallIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon,
  SaveIcon, CancelIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { apiErrMsg } from "@/api/client";

const { t } = useI18n();
const msg = useMessage();

const COLS = ["name", "host", "enabled", "sync_flags", "last_sync_at", "last_error", "actions"];
const { visibleKeys: vis, setVisible: setVis, reset: resetVis } = useColumnPrefs("zyxel", COLS, COLS);
const picker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "host", label: t("zyxel.host") },
  { key: "enabled", label: t("cols.status") },
  { key: "sync_flags", label: t("cols.sync_items") },
  { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const rows = ref<ZyxelFirewall[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<ZyxelFirewall | null>(null);

const diag = ref<ZyxelDiagnosis | null>(null);
const diagFor = ref<string>("");
const diagOpen = ref(false);

function blankForm() {
  return {
    name: "", host: "", port: 22, username: "", password: "",
    enabled: true,
    sync_arp: true, sync_dhcp: false, sync_policies: false, sync_nat: false, sync_addresses: false,
    sync_interval_seconds: 300, description: "",
    scope_subnet_ids: [] as string[],
  };
}
const form = ref(blankForm());

const subnetOptions = ref<{ label: string; value: string }[]>([]);
async function loadSubnetOptions() {
  try {
    const r = await listSubnets({ page: 1, pageSize: 500 });
    subnetOptions.value = r.items.map((s) => ({
      label: s.description ? `${s.cidr} — ${s.description}` : s.cidr, value: s.id }));
  } catch { /* silent */ }
}

async function refresh() {
  loading.value = true;
  try { rows.value = (await listZyxel()).items; }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  show.value = true;
}

function openEdit(r: ZyxelFirewall) {
  editing.value = r;
  form.value = {
    name: r.name, host: r.host, port: r.port, username: r.username, password: "",
    enabled: r.enabled,
    sync_arp: r.sync_arp, sync_dhcp: r.sync_dhcp, sync_policies: r.sync_policies,
    sync_nat: r.sync_nat, sync_addresses: r.sync_addresses,
    sync_interval_seconds: r.sync_interval_seconds,
    description: r.description ?? "",
    scope_subnet_ids: r.scope_subnet_ids ?? [],
  };
  show.value = true;
}

async function submit() {
  const payload: any = {
    name: form.value.name, host: form.value.host, port: form.value.port,
    username: form.value.username,
    enabled: form.value.enabled,
    sync_arp: form.value.sync_arp, sync_dhcp: form.value.sync_dhcp,
    sync_policies: form.value.sync_policies, sync_nat: form.value.sync_nat,
    sync_addresses: form.value.sync_addresses,
    sync_interval_seconds: form.value.sync_interval_seconds,
    description: form.value.description || undefined,
    scope_subnet_ids: form.value.scope_subnet_ids,
  };
  try {
    if (editing.value) {
      if (form.value.password) payload.password = form.value.password;
      await updateZyxel(editing.value.id, payload);
    } else {
      payload.password = form.value.password;
      await createZyxel(payload);
    }
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function test(r: ZyxelFirewall) {
  try {
    diag.value = await testZyxel(r.id);
    diagFor.value = r.name;
    diagOpen.value = true;
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function sync(id: string) {
  const name = rows.value.find((r) => r.id === id)?.name ?? id.slice(0, 8);
  try {
    await syncZyxel(id);
    msg.success(t("tasks.queued_toast", { kind: "Zyxel sync", target: name }));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function del(id: string) {
  try { await deleteZyxel(id); await refresh(); }
  catch { msg.error(t("errors.server")); }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

const SYNC_TAGS: [keyof ZyxelFirewall, string][] = [
  ["sync_arp", "ARP"], ["sync_dhcp", "DHCP"], ["sync_policies", "policy"],
  ["sync_nat", "NAT"], ["sync_addresses", "addr"],
];

const allCols = computed<DataTableColumns<ZyxelFirewall>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true } },
  {
    title: t("zyxel.host"), key: "host", minWidth: 160, ellipsis: { tooltip: true },
    render: (r) => `${r.host}:${r.port}`,
  },
  {
    title: t("common.status"), key: "enabled", width: 100,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: t("common.sync"), key: "sync_flags", minWidth: 170,
    render: (r) => h(NSpace, { size: 3, wrap: true }, () =>
      SYNC_TAGS.filter(([k]) => r[k]).map(([, label]) =>
        h(NTag, { size: "tiny", type: "info", bordered: false }, () => label))),
  },
  { title: t("cols.last_sync"), key: "last_sync_at", width: 165, render: (r) => fmtDateTime(r.last_sync_at) },
  {
    title: t("cols.last_error"), key: "last_error", minWidth: 150,
    ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—",
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 176,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(TestIcon, t("fortigate.diagnose"), () => test(r)),
      iconAction(SyncIcon, t("common.pull"), () => sync(r.id), "primary"),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));
const cols = computed<DataTableColumns<ZyxelFirewall>>(() =>
  allCols.value.filter((c: any) => vis.value.includes(c.key)),
);

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><FirewallIcon /></n-icon>
        <span>{{ t("zyxel.title") }}</span>
        <n-tag type="warning" size="small" :bordered="false">Beta</n-tag>
      </n-space>
    </template>

    <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
      {{ t("zyxel.intro") }}
    </n-alert>

    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <ColumnPicker :all="picker" :visible="vis" @update:visible="setVis" @reset="resetVis" />
    </n-space>

    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" :scroll-x="1100" />

    <!-- 新增 / 編輯 -->
    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : `${t('common.create')} — ${t('zyxel.title')}`"
             style="width: 560px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item :label="t('zyxel.host')">
          <n-input v-model:value="form.host" placeholder="192.0.2.1" />
        </n-form-item>
        <n-form-item :label="t('zyxel.port')">
          <n-input-number v-model:value="form.port" :min="1" :max="65535" style="width: 100%" />
        </n-form-item>
        <n-form-item :label="t('zyxel.username')">
          <n-input v-model:value="form.username" placeholder="admin" />
        </n-form-item>
        <n-form-item :label="editing ? t('zyxel.password_keep') : t('zyxel.password')">
          <n-input v-model:value="form.password" type="password" show-password-on="click" />
        </n-form-item>
        <div style="margin: -8px 0 12px">
          <span style="font-size: 11px; opacity: .7">{{ t("zyxel.password_hint") }}</span>
        </div>
        <n-form-item :label="t('fortigate.pull_what')">
          <n-space :size="16" :wrap="true">
            <n-checkbox v-model:checked="form.sync_arp">ARP</n-checkbox>
            <n-checkbox v-model:checked="form.sync_dhcp">{{ t("pfsense_admin.dhcp_leases") }}</n-checkbox>
            <n-checkbox v-model:checked="form.sync_policies">{{ t("fortigate.policies") }}</n-checkbox>
            <n-checkbox v-model:checked="form.sync_nat">NAT</n-checkbox>
            <n-checkbox v-model:checked="form.sync_addresses">{{ t("fortigate.addresses") }}</n-checkbox>
          </n-space>
        </n-form-item>
        <div style="margin: -8px 0 12px">
          <span style="font-size: 11px; opacity: .7">{{ t("zyxel.dhcp_hint") }}</span>
        </div>
        <n-form-item :label="t('adguard_admin.sync_interval')">
          <n-input-number v-model:value="form.sync_interval_seconds" :min="30" :max="86400" />
        </n-form-item>
        <n-form-item :label="t('common.enable')">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <n-form-item :label="t('adguard_admin.scope_subnets')">
          <div style="width: 100%">
            <n-select v-model:value="form.scope_subnet_ids" :options="subnetOptions"
                      multiple filterable clearable :placeholder="t('adguard_admin.scope_all')" />
            <ScopeOverlapWarning :scope-empty="!form.scope_subnet_ids?.length" />
          </div>
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <!-- 連線診斷 -->
    <n-modal v-model:show="diagOpen" preset="card"
             :title="`${t('fortigate.diagnose')} — ${diagFor}`" style="width: 640px">
      <template v-if="diag">
        <n-space vertical :size="10">
          <n-alert :type="diag.ok_count === diag.checks.length ? 'success' : 'warning'" :bordered="false">
            {{ t("fortigate.diag_summary", { ok: diag.ok_count, total: diag.checks.length }) }}
          </n-alert>
          <div v-for="c in diag.checks" :key="c.command" class="diag-row">
            <n-tag :type="c.ok ? 'success' : 'error'" size="small" :bordered="false">
              {{ c.ok ? "OK" : "ERR" }}
            </n-tag>
            <code>{{ c.command }}</code>
            <div v-if="c.ok" class="diag-sample">{{ c.sample }}</div>
            <span v-else class="diag-note">{{ c.error }}</span>
          </div>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<style scoped>
.diag-row { display: flex; flex-direction: column; gap: 4px; font-size: 13px; margin-bottom: 8px; }
.diag-sample { white-space: pre-wrap; font-family: monospace; font-size: 11px; opacity: .75;
               max-height: 90px; overflow-y: auto; background: rgba(128,128,128,.08);
               padding: 6px 8px; border-radius: 4px; }
.diag-note { opacity: .7; }
</style>
