<script setup lang="ts">
/**
 * Zyxel 防火牆檢視（唯讀，Beta，實驗性）：政策 / 位址物件。
 * 資料由 Zyxel 整合（SSH CLI）同步進來；本頁不連 Zyxel、也不修改任何設定。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NSelect, NTag, NIcon, NEmpty, NTabs, NTabPane,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { FirewallIcon } from "@/icons";
import {
  listZyxel, listZyxelPolicies, listZyxelAddresses,
  type ZyxelFirewall, type ZyxelPolicy, type ZyxelAddressObject,
} from "@/api/zyxel";
import { autoSort } from "@/composables/useTableSort";
import { apiErrMsg } from "@/api/client";

const { t } = useI18n();
const msg = useMessage();

const firewalls = ref<ZyxelFirewall[]>([]);
const fwId = ref<string | null>(null);
const policies = ref<ZyxelPolicy[]>([]);
const addresses = ref<ZyxelAddressObject[]>([]);
const loading = ref(false);

const fwOptions = computed(() => firewalls.value.map((f) => ({ label: f.name, value: f.id })));

async function loadFirewalls() {
  try {
    firewalls.value = (await listZyxel()).items;
    if (!fwId.value && firewalls.value.length) fwId.value = firewalls.value[0].id;
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function loadData() {
  if (!fwId.value) return;
  loading.value = true;
  try {
    [policies.value, addresses.value] = await Promise.all([
      listZyxelPolicies(fwId.value),
      listZyxelAddresses(fwId.value),
    ]);
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

watch(fwId, () => { void loadData(); });
onMounted(async () => { await loadFirewalls(); await loadData(); });

const policyCols = computed<DataTableColumns<ZyxelPolicy>>(() => autoSort([
  { title: "#", key: "rule_number", width: 70 },
  { title: t("common.name"), key: "name", minWidth: 140, ellipsis: { tooltip: true },
    render: (r) => r.name || "—" },
  { title: t("fortigate.col_action"), key: "action", width: 90, render: (r) => r.action ?? "—" },
  { title: t("common.status"), key: "status", width: 90, render: (r) => r.status ?? "—" },
  { title: "From / To", key: "from_zone", minWidth: 130,
    render: (r) => `${r.from_zone ?? "—"} → ${r.to_zone ?? "—"}` },
  { title: t("fortigate.col_src"), key: "source", minWidth: 140, ellipsis: { tooltip: true },
    render: (r) => r.source ?? "—" },
  { title: t("fortigate.col_dst"), key: "destination", minWidth: 140, ellipsis: { tooltip: true },
    render: (r) => r.destination ?? "—" },
  { title: t("fortigate.col_service"), key: "service", minWidth: 110, ellipsis: { tooltip: true },
    render: (r) => r.service ?? "—" },
  { title: t("sections.description"), key: "description", minWidth: 120,
    ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
]));

const addrCols = computed<DataTableColumns<ZyxelAddressObject>>(() => autoSort([
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
        <span>{{ t("zyxel.view_title") }}</span>
        <n-tag type="warning" size="small" :bordered="false">Beta</n-tag>
      </n-space>
    </template>

    <n-space style="margin-bottom: 12px" align="center">
      <n-select v-model:value="fwId" :options="fwOptions" style="width: 220px"
                :placeholder="t('zyxel.pick_firewall')" />
    </n-space>

    <n-empty v-if="!firewalls.length" :description="t('zyxel.none_configured')" />
    <n-tabs v-else type="line">
      <n-tab-pane name="policies" :tab="t('fortigate.policies')">
        <n-data-table :columns="policyCols" :data="policies" :loading="loading"
                      :bordered="false" :scroll-x="1100" />
      </n-tab-pane>
      <n-tab-pane name="addresses" :tab="t('fortigate.addresses')">
        <n-data-table :columns="addrCols" :data="addresses" :loading="loading"
                      :bordered="false" :scroll-x="700" />
      </n-tab-pane>
    </n-tabs>
  </n-card>
</template>
