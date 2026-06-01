<script setup lang="ts">
/**
 * RBAC 權限指派（admin）。
 * 選一個 使用者 / 角色(群組) → 看其授權 → 新增（物件類型 + 全部或指定物件 + 層級）→ 刪除。
 * 階層繼承由後端處理：授權上層（單位/區段/地點）自動涵蓋下層。
 */
import { computed, h, onMounted, ref, watch } from "vue";
import {
  NButton, NCard, NSpace, NSelect, NIcon, NTag, NRadioGroup, NRadioButton,
  NDataTable, NEmpty, NSwitch, useMessage, type DataTableColumns,
} from "naive-ui";
import { useI18n } from "vue-i18n";
import { apiClient } from "@/api/client";
import {
  listPermissions, upsertPermission, deletePermission, listRoles,
  type PermissionGrant, type Role, type PermObjectType, type PermLevel,
} from "@/api/permissions";
import { UsersIcon, DeleteIcon, PlusIcon, AdminIcon } from "@/icons";

const { t } = useI18n();
const msg = useMessage();

// 物件類型 → 清單 endpoint + label 欄位（ip 不提供逐一挑選，只能「全部」靠 cascade）
const TYPE_CFG: Record<string, { ep: string; label: string }> = {
  customer: { ep: "/api/v1/customers", label: "name" },
  section:  { ep: "/api/v1/sections", label: "name" },
  subnet:   { ep: "/api/v1/subnets", label: "cidr" },
  device:   { ep: "/api/v1/devices", label: "name" },
  rack:     { ep: "/api/v1/locations/racks", label: "name" },
  location: { ep: "/api/v1/locations/locations", label: "name" },
};

const roles = ref<Role[]>([]);
const users = ref<{ id: string; username: string; display_name: string | null }[]>([]);
const objectTypes = ref<PermObjectType[]>([]);
const levels = ref<PermLevel[]>(["read", "write", "admin"]);

const principalType = ref<"group" | "user">("group");
const principalId = ref<string | null>(null);
const grants = ref<PermissionGrant[]>([]);
const loadingGrants = ref(false);

// 物件 id → label（顯示授權用）；以及每類型的可選 options
const labelMap = ref<Record<string, string>>({});
const typeOptions = ref<Record<string, { label: string; value: string }[]>>({});

// 新增表單
const fObjType = ref<PermObjectType>("subnet");
const fAll = ref(true);
const fObjs = ref<string[]>([]);
const fLevel = ref<PermLevel>("read");
const saving = ref(false);

const principalOptions = computed(() =>
  principalType.value === "group"
    ? roles.value.map((r) => ({ label: r.is_builtin ? `★ ${r.name}` : r.name, value: r.id }))
    : users.value.map((u) => ({ label: u.display_name ? `${u.display_name} (${u.username})` : u.username, value: u.id })),
);
const typeSelOptions = computed(() => objectTypes.value.map((tt) => ({ label: t(`perm.type_${tt}`), value: tt })));
const levelSelOptions = computed(() => levels.value.map((l) => ({ label: t(`perm.level_${l}`), value: l })));
const fObjOptions = computed(() => typeOptions.value[fObjType.value] ?? []);
const fSpecificDisabled = computed(() => fObjType.value === "ip"); // ip 只能全部

async function loadLists() {
  const r = await listRoles();
  roles.value = r.roles;
  objectTypes.value = r.object_types;
  levels.value = r.levels;
  try {
    const { data } = await apiClient.get("/api/v1/users", { params: { page: 1, page_size: 500 } });
    users.value = (data.items ?? data).map((u: any) => ({ id: u.id, username: u.username, display_name: u.display_name }));
  } catch { /* ignore */ }
  // 預載各類型物件 → labelMap + options
  for (const [tt, cfg] of Object.entries(TYPE_CFG)) {
    try {
      const { data } = await apiClient.get(cfg.ep, { params: { page: 1, page_size: 500 } });
      const items = data.items ?? data ?? [];
      typeOptions.value[tt] = items.map((it: any) => ({ label: String(it[cfg.label] ?? it.id), value: it.id }));
      for (const it of items) labelMap.value[it.id] = String(it[cfg.label] ?? it.id);
    } catch { typeOptions.value[tt] = []; }
  }
}

async function loadGrants() {
  if (!principalId.value) { grants.value = []; return; }
  loadingGrants.value = true;
  try {
    grants.value = await listPermissions(principalType.value, principalId.value);
  } catch { msg.error(t("errors.network")); }
  finally { loadingGrants.value = false; }
}

watch([principalType, principalId], () => { void loadGrants(); });
watch(principalType, () => { principalId.value = null; grants.value = []; });
watch(fObjType, () => { fObjs.value = []; if (fSpecificDisabled.value) fAll.value = true; });

async function addGrant() {
  if (!principalId.value) { msg.warning(t("perm.pick_principal_first")); return; }
  saving.value = true;
  try {
    const targets: (string | null)[] = fAll.value ? [null] : fObjs.value;
    if (!fAll.value && targets.length === 0) { msg.warning(t("perm.pick_objects")); return; }
    for (const oid of targets) {
      await upsertPermission({
        object_type: fObjType.value, object_id: oid,
        principal_type: principalType.value, principal_id: principalId.value, level: fLevel.value,
      });
    }
    msg.success(t("common.saved"));
    fObjs.value = [];
    await loadGrants();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("perm.save_failed"));
  } finally { saving.value = false; }
}

async function removeGrant(id: string) {
  try { await deletePermission(id); await loadGrants(); }
  catch { msg.error(t("errors.network")); }
}

function targetLabel(g: PermissionGrant): string {
  if (g.object_id === null) return t("perm.all");
  return labelMap.value[g.object_id] ?? g.object_id.slice(0, 8);
}

const cols = computed<DataTableColumns<PermissionGrant>>(() => [
  { title: t("perm.col_type"), key: "object_type", width: 110,
    render: (r) => t(`perm.type_${r.object_type}`) },
  { title: t("perm.col_target"), key: "object_id", minWidth: 180,
    render: (r) => r.object_id === null
      ? h(NTag, { size: "small", type: "info" }, () => t("perm.all"))
      : targetLabel(r) },
  { title: t("perm.col_level"), key: "level", width: 110,
    render: (r) => h(NTag, { size: "small", type: r.level === "admin" ? "error" : r.level === "write" ? "warning" : "success" },
      () => t(`perm.level_${r.level}`)) },
  { title: t("common.actions"), key: "actions", width: 80, align: "center", className: "col-actions",
    render: (r) => h(NButton, { size: "small", quaternary: true, type: "error", onClick: () => removeGrant(r.id) },
      { icon: () => h(NIcon, null, () => h(DeleteIcon)) }) },
]);

onMounted(loadLists);
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center"><n-icon :size="22"><AdminIcon /></n-icon><span>{{ t("perm.title") }}</span></n-space>
    </template>

    <n-space vertical :size="14">
      <n-space align="center">
        <n-radio-group v-model:value="principalType" size="small">
          <n-radio-button value="group">{{ t("perm.role_group") }}</n-radio-button>
          <n-radio-button value="user">{{ t("perm.user") }}</n-radio-button>
        </n-radio-group>
        <n-select v-model:value="principalId" :options="principalOptions" filterable clearable
                  :placeholder="t('perm.pick_principal')" style="min-width: 280px" />
      </n-space>

      <template v-if="principalId">
        <n-card size="small" :title="t('perm.add_grant')">
          <n-space align="center" :wrap="true">
            <n-select v-model:value="fObjType" :options="typeSelOptions" style="width: 140px" />
            <n-switch v-model:value="fAll" :disabled="fSpecificDisabled">
              <template #checked>{{ t("perm.all") }}</template>
              <template #unchecked>{{ t("perm.specific") }}</template>
            </n-switch>
            <n-select v-if="!fAll" v-model:value="fObjs" :options="fObjOptions" multiple filterable
                      :placeholder="t('perm.pick_objects')" style="min-width: 300px; max-width: 460px" :max-tag-count="3" />
            <n-select v-model:value="fLevel" :options="levelSelOptions" style="width: 120px" />
            <n-button type="primary" :loading="saving" @click="addGrant">
              <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("common.create") }}
            </n-button>
          </n-space>
          <p style="opacity:.6;font-size:12px;margin:8px 0 0">{{ t("perm.cascade_hint") }}</p>
        </n-card>

        <n-data-table :columns="cols" :data="grants" :loading="loadingGrants" :bordered="false" :scroll-x="480" />
      </template>
      <n-empty v-else :description="t('perm.pick_principal')" style="margin: 32px" />
    </n-space>
  </n-card>
</template>
