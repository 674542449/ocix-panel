import api from '../api.js';
import { fmtDate, errMsg } from '../utils.js';

const { ref, reactive, onMounted } = window.Vue;
const { ElMessage, ElMessageBox } = window.ElementPlus || {};

export default {
  name: 'AuditTab',
  template: `
    <section class="pane">
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">操作记录</h2>
          <span class="who">共 {{ auditTotal }} 条</span>
        </div>
        <div class="toolbar">
          <el-select v-model="auditFilter.action" placeholder="全部操作" clearable style="width:180px;" @change="loadAudit(1)">
            <el-option v-for="a in auditActions" :key="a" :value="a" :label="a" />
          </el-select>
          <el-select v-model="auditFilter.result" placeholder="全部结果" clearable style="width:140px;" @change="loadAudit(1)">
            <el-option value="ok" label="成功" />
            <el-option value="fail" label="失败" />
          </el-select>
          <el-button @click="loadAudit(auditPage)" :loading="auditLoading">刷新</el-button>
          <el-button type="danger" plain @click="purgeAudit">清空日志</el-button>
        </div>
        <div class="table-wrap" v-if="auditLogs.length">
          <el-table :data="auditLogs" size="small" stripe border style="width:100%;">
            <el-table-column label="时间" width="160">
              <template #default="{row}"><span class="mono" style="color:#e2e8f0; font-weight:500;">{{ fmtDate(row.ts) }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="140">
              <template #default="{row}"><span style="color:#ffffff; font-weight:600;">{{ row.action }}</span></template>
            </el-table-column>
            <el-table-column label="结果" width="90">
              <template #default="{row}">
                <span class="state">
                  <i :class="['dot', row.result === 'ok' ? 'ok' : (row.result === 'fail' ? 'crit' : 'idle')]"></i>
                  <span class="state-label" style="font-weight:600;">{{ row.result === 'ok' ? '成功' : (row.result === 'fail' ? '失败' : row.result) }}</span>
                </span>
              </template>
            </el-table-column>
            <el-table-column label="用户" width="100">
              <template #default="{row}"><span style="color:#e2e8f0;">{{ row.username }}</span></template>
            </el-table-column>
            <el-table-column label="账户" width="110">
              <template #default="{row}"><span class="mono" style="color:#93c5fd; font-weight:600;">{{ row.profile }}</span></template>
            </el-table-column>
            <el-table-column label="目标" min-width="170" show-overflow-tooltip>
              <template #default="{row}"><span class="mono" style="color:#e2e8f0;">{{ row.target || '—' }}</span></template>
            </el-table-column>
            <el-table-column label="详情" min-width="190" show-overflow-tooltip>
              <template #default="{row}"><span style="color:#cbd5e1;">{{ row.detail }}</span></template>
            </el-table-column>
            <el-table-column label="来源 IP" width="130">
              <template #default="{row}"><span class="mono" style="color:#38bdf8; font-weight:600;">{{ row.ip || '—' }}</span></template>
            </el-table-column>
          </el-table>
          <div style="margin-top:16px; display:flex; justify-content:flex-end;">
            <el-pagination
              v-model:current-page="auditPage"
              :page-size="auditPageSize"
              :total="auditTotal"
              layout="total, prev, pager, next"
              @current-change="loadAudit"
            />
          </div>
        </div>
        <div class="empty" v-else-if="!auditLoading">
          <b>暂无审计日志</b>
        </div>
      </div>
    </section>
  `,
  setup() {
    const auditLogs = ref([]);
    const auditTotal = ref(0);
    const auditPage = ref(1);
    const auditPageSize = ref(20);
    const auditLoading = ref(false);
    const auditFilter = reactive({ action: '', result: '' });
    const auditActions = ref([]);

    async function loadAudit(page = 1) {
      auditLoading.value = true;
      auditPage.value = page;
      try {
        const { data } = await api.get('/api/system/audit', {
          params: {
            page,
            page_size: auditPageSize.value,
            action: auditFilter.action || undefined,
            result: auditFilter.result || undefined,
          },
        });
        auditLogs.value = data.items || [];
        auditTotal.value = data.total || 0;
        if (data.actions) auditActions.value = data.actions;
      } catch (e) {
        ElMessage?.error(errMsg(e, '加载审计日志失败'));
      } finally {
        auditLoading.value = false;
      }
    }

    async function purgeAudit() {
      try {
        await ElMessageBox.confirm('确认清空所有审计日志吗？此操作不可撤销。', '清空日志', { type: 'warning' });
        await api.delete('/api/system/audit');
        ElMessage?.success('审计日志已清空');
        loadAudit(1);
      } catch {}
    }

    onMounted(() => loadAudit(1));

    return {
      auditLogs,
      auditTotal,
      auditPage,
      auditPageSize,
      auditLoading,
      auditFilter,
      auditActions,
      loadAudit,
      purgeAudit,
      fmtDate,
    };
  }
};
