import api from './api.js';
import { errMsg } from './utils.js';

const { ref, reactive, computed } = window.Vue;

// 用户与鉴权
export const token = ref(localStorage.getItem('ocix_token') || '');
export const user = ref(localStorage.getItem('ocix_user') || '');
export const appVersion = ref('');
export const serviceOk = ref(true);
export const serviceHint = ref('与面板后端通信正常');
export const globalLoading = ref(false);

// 导航
export const activeTab = ref('instances');
export const lastUpdated = ref('');

// 账户与隔离区
export const profiles = ref([]);
export const profilesLoading = ref(false);
export const currentProfile = ref(localStorage.getItem('ocix_profile') || '');
export const lockedProfile = ref(null);
export const compartments = ref([]);
export const compsLoading = ref(false);
export const compartmentId = ref('');
export const subtree = ref(false);

// 等级状态
export const tier = ref({});
export const tiers = ref({});
export const tierLoading = ref(false);
export const tierBusy = ref(false);

// 存储卷孤儿角标
export const storage = ref(null);
export const orphanBadge = computed(() => (storage.value && storage.value.summary && storage.value.summary.orphan_count) || 0);

export function scopeParams(extra = {}) {
  const p = { profile: currentProfile.value, subtree: subtree.value, ...extra };
  if (compartmentId.value) p.compartment_id = compartmentId.value;
  return p;
}

export async function loadProfiles() {
  profilesLoading.value = true;
  try {
    const { data } = await api.get('/api/profiles');
    profiles.value = data.profiles || [];
    const names = profiles.value.map(p => p.name);
    
    // 填充等级数据
    const initialTiers = {};
    for (const p of (data.profiles || [])) {
      if (p.tier) initialTiers[p.name] = p.tier;
    }
    tiers.value = { ...initialTiers, ...tiers.value };

    if (lockedProfile.value && names.includes(lockedProfile.value)) {
      currentProfile.value = lockedProfile.value;
    } else if (!names.includes(currentProfile.value)) {
      currentProfile.value = names[0] || '';
    }
    if (currentProfile.value && tiers.value[currentProfile.value]) {
      tier.value = tiers.value[currentProfile.value];
    }
  } catch (e) {
    if (window.ElementPlus?.ElMessage) {
      window.ElementPlus.ElMessage.error(errMsg(e, '账户列表加载失败'));
    }
  } finally {
    profilesLoading.value = false;
  }
}

export async function loadCompartments() {
  compartments.value = [];
  if (!currentProfile.value) return;
  compsLoading.value = true;
  try {
    const { data } = await api.get('/api/instances/compartments', {
      params: { profile: currentProfile.value },
    });
    compartments.value = data.compartments || [];
  } catch (e) {
    // 忽略异常
  } finally {
    compsLoading.value = false;
  }
}

export async function loadLock() {
  try {
    const { data } = await api.get('/api/profiles/lock');
    lockedProfile.value = data.locked || null;
  } catch {}
}
