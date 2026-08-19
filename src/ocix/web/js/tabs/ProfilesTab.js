import api from '../api.js';
import { profiles, profilesLoading, currentProfile, lockedProfile, tiers, tier, tierBusy, loadProfiles, loadLock } from '../store.js';
import { TIER_DOT, TIER_SHORT } from '../constants.js';
import { errMsg } from '../utils.js';

const { ref, reactive } = window.Vue;
const { ElMessage, ElMessageBox } = window.ElementPlus || {};

export default {
  name: 'ProfilesTab',
  template: `
    <section class="pane">
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">已配置账户</h2>
          <div style="display:flex; align-items:center; gap:8px;">
            <span class="who" v-if="profiles.length">{{ profiles.length }} 个</span>
            <el-button size="small" @click="loadAllTiers(true)" :disabled="!profiles.length || tierBusy">
              {{ tierBusy ? '检测中…' : (Object.keys(tiers).length ? '重新检测全部' : '检测全部等级') }}
            </el-button>
          </div>
        </div>

        <div class="table-wrap" v-if="profiles.length">
          <el-table :data="profiles" size="small" stripe border style="width:100%;">
            <el-table-column prop="name" label="名称" width="120" />
            <el-table-column label="账户等级" width="128">
              <template #default="{row}">
                <span class="state" :title="tierTitle(row.name)">
                  <i :class="['dot', tierDot(row.name)]"></i>
                  <span class="state-label">{{ tierText(row.name) }}</span>
                </span>
              </template>
            </el-table-column>
            <el-table-column label="区域" width="126">
              <template #default="{row}"><span class="mono">{{ row.region || '—' }}</span></template>
            </el-table-column>
            <el-table-column label="User OCID" min-width="190" show-overflow-tooltip>
              <template #default="{row}"><span class="mono">{{ row.user_ocid || '—' }}</span></template>
            </el-table-column>
            <el-table-column label="Fingerprint" min-width="140" show-overflow-tooltip>
              <template #default="{row}"><span class="mono">{{ row.fingerprint || '—' }}</span></template>
            </el-table-column>
            <el-table-column label="私钥" width="86">
              <template #default="{row}">
                <span class="state">
                  <i :class="['dot', row.key_exists ? 'ok' : 'crit']"></i>
                  <span class="state-label">{{ row.key_exists ? '在位' : '缺失' }}</span>
                </span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="298" fixed="right">
              <template #default="{row}">
                <div class="row-actions">
                  <el-button size="small" :type="lockedProfile === row.name ? 'warning' : ''"
                             :loading="isBusy('lock:' + row.name)"
                             @click="toggleLock(row.name)">
                    {{ lockedProfile === row.name ? '解除锁定' : '锁定' }}
                  </el-button>
                  <el-button size="small" :loading="isBusy('tier:' + row.name)"
                             :disabled="tierBusy" @click="loadOneTier(row.name)">检测等级</el-button>
                  <el-button size="small" :loading="isBusy('test:' + row.name)"
                             @click="testProfile(row.name)">校验</el-button>
                  <el-button size="small" type="danger" :loading="isBusy('del:' + row.name)"
                             @click="delProfile(row.name)">删除</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <div class="empty" v-else>
          <b>暂无配置账户</b>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h2 class="card-title">导入账户</h2></div>
        <el-alert type="info" :closable="false" show-icon title="粘贴 ~/.oci/config 配置段落并附上私钥" />
        <div style="margin:16px 0;">
          <el-form label-position="top">
            <el-form-item label="Profile 名称">
              <el-input v-model="importForm.profile_name" placeholder="留空则使用配置中的段落名" />
            </el-form-item>
            <el-form-item label="配置内容">
              <el-input v-model="importForm.config_text" type="textarea" :rows="9" class="mono"
                        placeholder="[DEFAULT]&#10;user=ocid1.user.oc1..xxxx&#10;fingerprint=xx:xx:xx&#10;key_file=~/.oci/oci_api_key.pem&#10;tenancy=ocid1.tenancy.oc1..xxxx&#10;region=us-ashburn-1" />
            </el-form-item>
            <el-form-item label="私钥文本（PEM 格式内容）">
              <el-input v-model="importForm.key_text" type="textarea" :rows="5" class="mono"
                        placeholder="-----BEGIN RSA PRIVATE KEY-----&#10;...&#10;-----END RSA PRIVATE KEY-----" />
            </el-form-item>
            <el-form-item label="私钥密码短语（若私钥有加密）">
              <el-input v-model="importForm.pass_phrase" type="password" show-password placeholder="若私钥未加密可留空" />
            </el-form-item>
            <el-button type="primary" @click="doImport" :loading="importing">立即导入并验证</el-button>
          </el-form>
        </div>
      </div>
    </section>
  `,
  setup() {
    const busyKeys = ref(new Set());
    const importing = ref(false);
    const importForm = reactive({
      profile_name: '',
      config_text: '',
      key_text: '',
      pass_phrase: '',
    });

    const isBusy = (k) => busyKeys.value.has(k);
    const setBusy = (k, v) => {
      const next = new Set(busyKeys.value);
      if (v) next.add(k); else next.delete(k);
      busyKeys.value = next;
    };

    const tierDot = (name) => {
      const t = tiers.value[name];
      return t ? (TIER_DOT[t.tier] || 'idle') : 'idle';
    };
    const tierText = (name) => {
      const t = tiers.value[name];
      if (t === undefined) return tierBusy.value ? '检测中…' : '未检测';
      return TIER_SHORT[t.tier] || '未知';
    };
    const tierTitle = (name) => {
      const t = tiers.value[name];
      return t ? [t.label].concat(t.reasons || []).join('\n') : '还没有检测过';
    };

    async function loadOneTier(name) {
      if (tierBusy.value) return ElMessage?.warning('正在批量检测中，请稍候');
      setBusy('tier:' + name, true);
      try {
        const { data } = await api.get(`/api/profiles/${encodeURIComponent(name)}/tier`, {
          params: { limits: false, refresh: true },
        });
        tiers.value = { ...tiers.value, [name]: data };
        if (name === currentProfile.value) tier.value = data;
        const label = data.label || TIER_SHORT[data.tier] || '未知';
        ElMessage?.success(`${name}：${label}`);
      } catch (e) {
        tiers.value = {
          ...tiers.value,
          [name]: { tier: 'unknown', label: '检测失败', reasons: [errMsg(e, '检测失败')] },
        };
        ElMessage?.error(errMsg(e, `${name} 等级检测失败`));
      } finally {
        setBusy('tier:' + name, false);
      }
    }

    async function loadAllTiers(force = true) {
      const names = profiles.value.map(p => p.name);
      if (!names.length || tierBusy.value) return;
      if (force) tiers.value = {};
      tierBusy.value = true;
      try {
        for (const name of names) {
          if (!force && tiers.value[name]) continue;
          try {
            const { data } = await api.get(`/api/profiles/${encodeURIComponent(name)}/tier`, {
              params: { limits: false, refresh: !!force },
            });
            tiers.value = { ...tiers.value, [name]: data };
            if (name === currentProfile.value) tier.value = data;
          } catch (e) {
            tiers.value = {
              ...tiers.value,
              [name]: { tier: 'unknown', label: '检测失败', reasons: [errMsg(e, '检测失败')] },
            };
          }
        }
      } finally {
        tierBusy.value = false;
      }
    }

    async function toggleLock(name) {
      setBusy('lock:' + name, true);
      try {
        if (lockedProfile.value === name) {
          await api.delete('/api/profiles/lock');
          lockedProfile.value = null;
          ElMessage?.success('已解除锁定');
        } else {
          await api.post(`/api/profiles/${encodeURIComponent(name)}/lock`);
          lockedProfile.value = name;
          currentProfile.value = name;
          ElMessage?.success(`已锁定账户：${name}`);
        }
      } catch (e) {
        ElMessage?.error(errMsg(e, '锁定操作失败'));
      } finally {
        setBusy('lock:' + name, false);
      }
    }

    async function testProfile(name) {
      setBusy('test:' + name, true);
      try {
        await api.post(`/api/profiles/${encodeURIComponent(name)}/test`);
        ElMessage?.success(`${name} 连通性校验成功`);
        loadProfiles();
      } catch (e) {
        ElMessage?.error(errMsg(e, `${name} 校验失败`));
      } finally {
        setBusy('test:' + name, false);
      }
    }

    async function delProfile(name) {
      try {
        await ElMessageBox.confirm(`确认删除账户「${name}」的配置与私钥吗？`, '删除账户', { type: 'warning' });
        setBusy('del:' + name, true);
        await api.delete(`/api/profiles/${encodeURIComponent(name)}`);
        ElMessage?.success(`账户 ${name} 已删除`);
        loadProfiles();
      } catch {}
      finally {
        setBusy('del:' + name, false);
      }
    }

    async function doImport() {
      if (!importForm.config_text.trim()) return ElMessage?.warning('请粘贴配置内容');
      importing.value = true;
      try {
        const formData = new FormData();
        formData.append('config_text', importForm.config_text);
        if (importForm.profile_name) formData.append('profile_name', importForm.profile_name);
        if (importForm.key_text) formData.append('key_text', importForm.key_text);
        if (importForm.pass_phrase) formData.append('pass_phrase', importForm.pass_phrase);

        const { data } = await api.post('/api/profiles/import', formData);
        ElMessage?.success(`账户 ${data.profile} 导入并校验成功！`);
        importForm.config_text = '';
        importForm.key_text = '';
        importForm.pass_phrase = '';
        importForm.profile_name = '';
        loadProfiles();
      } catch (e) {
        ElMessage?.error(errMsg(e, '导入失败'));
      } finally {
        importing.value = false;
      }
    }

    return {
      profiles,
      profilesLoading,
      lockedProfile,
      tiers,
      tierBusy,
      importForm,
      importing,
      isBusy,
      tierDot,
      tierText,
      tierTitle,
      loadOneTier,
      loadAllTiers,
      toggleLock,
      testProfile,
      delProfile,
      doImport,
    };
  }
};
