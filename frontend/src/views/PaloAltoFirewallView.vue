<script setup lang="ts">
/**
 * Palo Alto (PAN-OS) 防火牆檢視（唯讀，Beta，實驗性）：政策 / 位址物件。
 * 資料由 Palo Alto 整合同步進來；本頁不連防火牆、也不修改任何設定。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NSelect, NTag, NIcon, NEmpty, NTabs, NTabPane,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { FirewallIcon } from "@/icons";
import {
  listPaloAlto, listPaloAltoPolicies, listPaloAltoAddresses,
  type PaloAltoFirewall, type PaloAltoPolicy, type PaloAltoAddressObject,
} from "@/api/paloalto";
import { autoSort } from "@/composables/useTableSort";
import { apiErrMsg } from "@/api/client";

const { t } = useI18n();
const msg = useMessage();

const firewalls = ref<PaloAltoFirewall[]>([]);
const fwId = ref<string | null>(null);
const policies = ref<PaloAltoPolicy[]>([]);
const addresses = ref<PaloAltoAddressObject[]>([]);
const loading = ref(false);

const fwOptions = computed(() => firewalls.value.map((f) => ({ label: f.name, value: f.id })));

async function loadFirewalls() {
  try {
    firewalls.value = (await listPaloAlto()).items;
    if (!fwId.value && firewalls.value.length) fwId.value = firewalls.value[0].id;
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function loadData() {
  if (!fwId.value) return;
  loading.value = true;
  try {
    [policies.value, addresses.value] = await Promise.all([
      listPaloAltoPolicies(fwId.value),
      listPaloAltoAddresses(fwId.value),
    ]);
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

watch(fwId, () => { void loadData(); });
onMounted(async () => { await loadFirewalls(); await loadData(); });

const policyCols = computed<DataTableColumns<PaloAltoPolicy>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 140, ellipsis: { tooltip: true } },
  { title: t("fortigate.col_action"), key: "action", width: 90, render: (r) => r.action ?? "—" },
  {
    title: t("common.status"), key: "disabled", width: 90,
    render: (r) => r.disabled ? t("common.disabled") : t("common.enabled"),
  },
  { title: "From / To", key: "from_zone", minWidth: 130,
    render: (r) => `${r.from_zone ?? "—"} → ${r.to_zone ?? "—"}` },
  { title: t("fortigate.col_src"), key: "source", minWidth: 130, ellipsis: { tooltip: true },
    render: (r) => r.source ?? "—" },
  { title: t("fortigate.col_dst"), key: "destination", minWidth: 130, ellipsis: { tooltip: true },
    render: (r) => r.destination ?? "—" },
  { title: "Application", key: "application", minWidth: 130, ellipsis: { tooltip: true },
    render: (r) => r.application ?? "—" },
  { title: t("fortigate.col_service"), key: "service", minWidth: 110, ellipsis: { tooltip: true },
    render: (r) => r.service ?? "—" },
  { title: t("sections.description"), key: "description", minWidth: 120,
    ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
]));

const addrCols = computed<DataTableColumns<PaloAltoAddressObject>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("common.type"), key: "obj_type", width: 110, render: (r) => r.obj_type ?? "—" },
  { title: t("fortigate.col_value"), key: "value", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => r.value ?? "—" },
]));
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><FirewallIcon /></n-icon>
        <span>{{ t("paloalto.view_title") }}</span>
        <n-tag type="warning" size="small" :bordered="false">Beta</n-tag>
      </n-space>
    </template>

    <n-space style="margin-bottom: 12px" align="center">
      <n-select v-model:value="fwId" :options="fwOptions" style="width: 220px"
                :placeholder="t('paloalto.pick_firewall')" />
    </n-space>

    <n-empty v-if="!firewalls.length" :description="t('paloalto.none_configured')" />
    <n-tabs v-else type="line">
      <n-tab-pane name="policies" :tab="t('fortigate.policies')">
        <n-data-table :columns="policyCols" :data="policies" :loading="loading"
                      :bordered="false" :scroll-x="1150" />
      </n-tab-pane>
      <n-tab-pane name="addresses" :tab="t('fortigate.addresses')">
        <n-data-table :columns="addrCols" :data="addresses" :loading="loading"
                      :bordered="false" :scroll-x="700" />
      </n-tab-pane>
    </n-tabs>
  </n-card>
</template>
