import api from '../api.js';
import { currentProfile } from '../store.js';
import { errMsg } from '../utils.js';

const { ref, reactive, computed, onMounted, watch } = window.Vue;
const { ElMessage } = window.ElementPlus || {};

export default {
  name: 'PasswordTab',
  template: `
    <section class="pane">
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">Oracle 账号密码有效期</h2>
          <el-button size="small" @click="loadConsolePolicy(true)" :loading="consoleLoading">重新读取</el-button>
        </div>

        <template v-if="consolePolicy.supported">
          <div class="kv" style="margin-bottom:12px;">
            <span class="state">
              <i :class="['dot', consoleDays > 0 ? 'attn' : 'ok']"></i>
              <span class="state-label" style="font-size:13px;">
                {{ consoleDays > 0 ? consoleDays + ' 天后必须改密' : '永不过期' }}
              </span>
            </span>
            <span class="who">账户：{{ currentProfile }}</span>
          </div>

          <div class="table-wrap" v-if="consolePolicy.policies.length > 1">
            <el-table :data="consolePolicy.policies" size="small" stripe border>
              <el-table-column prop="domain_name" label="Identity Domain" min-width="140" />
              <el-table-column prop="name" label="策略" min-width="160" />
              <el-table-column label="有效期" width="110">
                <template #default="{row}">
                  <span class="mono">{{ row.expires_after_days || '永不过期' }}</span>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <el-form label-position="top" @submit.prevent style="margin-top:10px;">
            <el-form-item label="密码有效天数（0 为永不过期）">
              <el-input-number v-model="consoleForm.days" :min="0" :max="3650" :step="30" style="width:180px;" />
            </el-form-item>
            <el-button type="primary" @click="saveConsolePolicy" :loading="consoleSaving">
              应用到 Oracle 账号
            </el-button>
            <el-button @click="consoleForm.days = 0; saveConsolePolicy()" :loading="consoleSaving">
              设为永不过期
            </el-button>
          </el-form>
        </template>

        <div class="empty" v-else-if="!consoleLoading">
          <b>暂无密码策略数据</b>
          <span>传统 IAM 租户不支持修改密码策略，或未读取到有效策略</span>
        </div>
      </div>
    </section>
  `,
  setup() {
    const consolePolicy = ref({ supported: false, policies: [] });
    const consoleLoading = ref(false);
    const consoleSaving = ref(false);
    const consoleForm = reactive({ days: 0 });

    const consoleDays = computed(() => {
      const list = (consolePolicy.value.policies || []).filter(p => p.expires_after_days > 0);
      if (!list.length) return 0;
      return Math.min(...list.map(p => p.expires_after_days));
    });

    async function loadConsolePolicy(force = false) {
      if (!currentProfile.value) return;
      consoleLoading.value = true;
      try {
        const { data } = await api.get(
          `/api/profiles/${encodeURIComponent(currentProfile.value)}/console-password-policy`
        );
        consolePolicy.value = data;
        if (data.policies && data.policies.length) {
          consoleForm.days = data.policies[0].expires_after_days || 0;
        }
      } catch (e) {
        consolePolicy.value = { supported: false, policies: [] };
        if (force) ElMessage?.error(errMsg(e, '读取密码策略失败'));
      } finally {
        consoleLoading.value = false;
      }
    }

    async function saveConsolePolicy() {
      if (!currentProfile.value) return;
      consoleSaving.value = true;
      try {
        await api.put(
          `/api/profiles/${encodeURIComponent(currentProfile.value)}/console-password-policy`,
          { expires_after_days: consoleForm.days }
        );
        ElMessage?.success('密码有效期策略已应用');
        loadConsolePolicy(true);
      } catch (e) {
        ElMessage?.error(errMsg(e, '修改密码策略失败'));
      } finally {
        consoleSaving.value = false;
      }
    }

    watch(currentProfile, () => loadConsolePolicy(false));
    onMounted(() => loadConsolePolicy(false));

    return {
      currentProfile,
      consolePolicy,
      consoleLoading,
      consoleSaving,
      consoleForm,
      consoleDays,
      loadConsolePolicy,
      saveConsolePolicy,
    };
  }
};
