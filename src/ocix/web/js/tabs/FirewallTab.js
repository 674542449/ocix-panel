import api from '../api.js';
import { currentProfile, scopeParams } from '../store.js';
import { errMsg } from '../utils.js';

const { ref, reactive, onMounted, watch } = window.Vue;
const { ElMessage, ElMessageBox } = window.ElementPlus || {};

export default {
  name: 'FirewallTab',
  template: `
    <section class="pane">
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">安全列表规则</h2>
          <div class="toolbar" style="margin-bottom:0;">
            <el-select v-model="fwInstance" placeholder="选择实例" style="width:260px;" @change="loadFirewall">
              <el-option v-for="i in instances" :key="i.id" :value="i.id" :label="i.display_name" />
            </el-select>
            <el-button @click="loadFirewall" :loading="fwLoading" :disabled="!fwInstance">刷新</el-button>
          </div>
        </div>

        <template v-if="firewall">
          <div class="fw-verdict" :class="firewall.all_open_v4 ? 'open' : 'partial'">
            <div>
              <b>{{ firewall.verdict }}</b>
              <span class="mono">子网 {{ firewall.subnet_name }} · IPv6 {{ firewall.ipv6_enabled ? '已开通' : '未开通' }}</span>
            </div>
            <div class="fw-actions">
              <el-button v-if="!firewall.all_open_v4" type="warning" :loading="fwBusy" @click="allowAll">放行全部端口</el-button>
              <el-button v-else type="primary" plain :loading="fwBusy" @click="revokeAll">撤销全放行</el-button>
              <el-button plain :loading="fwBusy" @click="clearRules">清空规则</el-button>
            </div>
          </div>

          <div class="table-wrap" v-if="firewall.ingress_rules.length">
            <el-table :data="firewall.ingress_rules" size="small" stripe border style="width:100%;">
              <el-table-column label="协议" width="110">
                <template #default="{row}"><span class="mono">{{ row.protocol }}</span></template>
              </el-table-column>
              <el-table-column label="来源" width="160">
                <template #default="{row}"><span class="mono">{{ row.source }}</span></template>
              </el-table-column>
              <el-table-column label="端口" width="140">
                <template #default="{row}"><span class="mono">{{ row.ports }}</span></template>
              </el-table-column>
              <el-table-column label="说明" min-width="180">
                <template #default="{row}">{{ row.description || '—' }}</template>
              </el-table-column>
              <el-table-column label="操作" width="88" fixed="right">
                <template #default="{ $index }">
                  <el-button size="small" text class="term-btn" :loading="fwBusy" @click="deleteRule($index)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
          <div class="empty" v-else><b>无入站规则</b></div>

          <div class="card-head" style="margin-top:18px;">
            <h2 class="card-title">新增入站规则</h2>
          </div>
          <div class="rule-form">
            <el-select v-model="ruleForm.protocol" style="width:116px;">
              <el-option v-for="p in ['ALL','TCP','UDP','ICMP','ICMPv6']" :key="p" :value="p" :label="p === 'ALL' ? '全部协议' : p" />
            </el-select>
            <el-input-number v-model="ruleForm.port_from" :min="1" :max="65535"
                             :disabled="ruleForm.protocol === 'ICMP' || ruleForm.protocol === 'ICMPv6' || ruleForm.protocol === 'ALL'"
                             controls-position="right" style="width:118px;" />
            <span class="rule-sep">至</span>
            <el-input-number v-model="ruleForm.port_to" :min="1" :max="65535"
                             :disabled="ruleForm.protocol === 'ICMP' || ruleForm.protocol === 'ICMPv6' || ruleForm.protocol === 'ALL'"
                             controls-position="right" style="width:118px;" />
            <el-select v-model="ruleForm.source" style="width:158px;" filterable allow-create>
              <el-option value="0.0.0.0/0" label="0.0.0.0/0（任意 IPv4）" />
              <el-option value="::/0" label="::/0（任意 IPv6）" />
            </el-select>
            <el-button type="primary" :loading="fwBusy" @click="addRule">添加</el-button>
          </div>
        </template>
        <div class="empty" v-else-if="!fwLoading">
          <b>请选择实例以查看防火墙规则</b>
        </div>
      </div>
    </section>
  `,
  setup() {
    const instances = ref([]);
    const fwInstance = ref('');
    const firewall = ref(null);
    const fwLoading = ref(false);
    const fwBusy = ref(false);
    const ruleForm = reactive({
      protocol: 'TCP',
      port_from: 80,
      port_to: 80,
      source: '0.0.0.0/0',
    });

    function fwPayload(extra = {}) {
      const inst = instances.value.find((i) => i.id === fwInstance.value);
      return {
        profile: currentProfile.value,
        instance_id: fwInstance.value,
        compartment_id: inst ? inst.compartment_id : undefined,
        ...extra,
      };
    }

    async function loadInstances() {
      if (!currentProfile.value) return;
      try {
        const { data } = await api.get('/api/instances', { params: scopeParams() });
        instances.value = data.instances || [];
        if (instances.value.length && !fwInstance.value) {
          fwInstance.value = instances.value[0].id;
          loadFirewall();
        }
      } catch {}
    }

    async function loadFirewall() {
      if (!fwInstance.value || !currentProfile.value) return;
      fwLoading.value = true;
      try {
        const { data } = await api.get('/api/provision/firewall', {
          params: fwPayload(),
        });
        firewall.value = data;
      } catch (e) {
        firewall.value = null;
        ElMessage?.error(errMsg(e, '获取防火墙配置失败'));
      } finally {
        fwLoading.value = false;
      }
    }

    async function allowAll() {
      fwBusy.value = true;
      try {
        const { data } = await api.post('/api/provision/firewall/allow-all', fwPayload());
        firewall.value = data.status;
        ElMessage?.success('已放行全部端口');
      } catch (e) {
        ElMessage?.error(errMsg(e, '放行端口失败'));
      } finally {
        fwBusy.value = false;
      }
    }

    async function revokeAll() {
      fwBusy.value = true;
      try {
        const { data } = await api.post('/api/provision/firewall/revoke-all', fwPayload({ keep_ssh: true }));
        firewall.value = data.status;
        ElMessage?.success(`已移除全放行规则，保留了 SSH (22)`);
      } catch (e) {
        ElMessage?.error(errMsg(e, '撤销全放行失败'));
      } finally {
        fwBusy.value = false;
      }
    }

    async function clearRules() {
      try {
        await ElMessageBox.confirm('清空规则后未放行的端口将无法访问。确定清空？', '清空规则', {
          type: 'warning',
        });
      } catch {
        return;
      }
      fwBusy.value = true;
      try {
        const { data } = await api.post('/api/provision/firewall/clear', fwPayload({ keep_ssh: true }));
        firewall.value = data.status;
        ElMessage?.success(`已清空 ${data.removed} 条规则`);
      } catch (e) {
        ElMessage?.error(errMsg(e, '清空规则失败'));
      } finally {
        fwBusy.value = false;
      }
    }

    async function addRule() {
      if (ruleForm.protocol !== 'ICMP' && ruleForm.protocol !== 'ICMPv6' && ruleForm.protocol !== 'ALL' && ruleForm.port_to < ruleForm.port_from) {
        return ElMessage?.warning('结束端口不能小于起始端口');
      }
      fwBusy.value = true;
      try {
        const { data } = await api.post('/api/provision/firewall/rules', fwPayload({
          protocol: ruleForm.protocol,
          port_from: ruleForm.port_from,
          port_to: ruleForm.port_to,
          source: ruleForm.source,
        }));
        firewall.value = data.status;
        ElMessage?.success(data.added ? '规则已添加' : '相同的规则已存在');
      } catch (e) {
        ElMessage?.error(errMsg(e, '添加规则失败'));
      } finally {
        fwBusy.value = false;
      }
    }

    async function deleteRule(index) {
      fwBusy.value = true;
      try {
        const { data } = await api.post('/api/provision/firewall/rules/delete', fwPayload({ index }));
        firewall.value = data.status;
        ElMessage?.success('规则已删除');
      } catch (e) {
        ElMessage?.error(errMsg(e, '删除规则失败'));
      } finally {
        fwBusy.value = false;
      }
    }

    watch(currentProfile, () => {
      fwInstance.value = '';
      firewall.value = null;
      loadInstances();
    });

    onMounted(loadInstances);

    return {
      instances,
      fwInstance,
      firewall,
      fwLoading,
      fwBusy,
      ruleForm,
      loadFirewall,
      allowAll,
      revokeAll,
      clearRules,
      addRule,
      deleteRule,
    };
  }
};
