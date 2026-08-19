import api from '../api.js';
import { currentProfile } from '../store.js';
import { fmtDay, errMsg } from '../utils.js';

const { ref, onMounted, watch } = window.Vue;
const { ElMessage } = window.ElementPlus || {};

export default {
  name: 'BillingTab',
  template: `
    <section class="pane">
      <div class="fin-tier-banner">
        <div>
          <b style="font-size:14px; color:#ffffff;">Oracle Cloud 财务与成本中心</b>
          <div style="font-size:12px; color:var(--text-3); margin-top:3px;">
            Always Free（免费层）账户享受永久免费配额，无账单扣费；升级号（按量付费/付费订阅）提供全方位成本监控、期间对账与账单导出。
          </div>
        </div>
        <div style="display:flex; align-items:center; gap:8px;">
          <el-button size="small" @click="loadBilling(true)" :loading="billingLoading">刷新财务数据</el-button>
        </div>
      </div>

      <!-- 期间成本与预测分析 -->
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">成本分析与月度支出预测</h2>
          <el-button size="small" text bg @click="downloadCostCsv">导出期间成本明细 (CSV)</el-button>
        </div>
        <div style="margin-bottom:14px;">
          <el-radio-group v-model="selectedPeriod" size="small" @change="loadPeriodCost">
            <el-radio-button value="current_month">本月至今</el-radio-button>
            <el-radio-button value="last_month">上月整月</el-radio-button>
            <el-radio-button value="last_3_months">近 3 个月</el-radio-button>
            <el-radio-button value="last_6_months">近 6 个月</el-radio-button>
          </el-radio-group>
        </div>
        <div class="stats" style="margin-bottom:8px;">
          <div class="stat">
            <div class="v" style="color:#38bdf8;">{{ periodCost.currency || 'USD' }} {{ periodCost.total || '0.00' }}</div>
            <div class="k">期间实际消费</div>
          </div>
          <div class="stat" v-if="selectedPeriod === 'current_month'">
            <div class="v" style="color:#fde047;">{{ periodCost.currency || 'USD' }} {{ periodCost.forecast_total || '0.00' }}</div>
            <div class="k">本月预估总消费</div>
          </div>
          <div class="stat">
            <div class="v">{{ (periodCost.by_service || []).length }}</div>
            <div class="k">涉及计费服务</div>
          </div>
        </div>
      </div>

      <!-- 各云服务消费明细与占比 -->
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">云服务消费分布与排行</h2>
          <span class="who" v-if="periodCost.start_date">
            {{ periodCost.start_date }} 至 {{ periodCost.end_date }}
          </span>
        </div>
        <div v-if="(periodCost.by_service || []).length">
          <div v-for="it in periodCost.by_service" :key="it.service" class="cost-bar-wrap">
            <div class="cost-bar-head">
              <span style="font-weight:600; color:#f8fafc;">{{ it.service }}</span>
              <span class="mono" style="font-weight:600; color:#38bdf8;">
                {{ periodCost.currency || 'USD' }} {{ it.amount }}
                <small style="color:var(--text-3); font-weight:400; margin-left:6px;">
                  ({{ (periodCost.total > 0 ? (it.amount / periodCost.total * 100).toFixed(1) : '0.0') }}%)
                </small>
              </span>
            </div>
            <div class="cost-bar-track">
              <div class="cost-bar-fill" :style="{ width: Math.min(100, Math.max(2, (periodCost.total > 0 ? (it.amount / periodCost.total * 100) : 0))) + '%', background: '#38bdf8' }"></div>
            </div>
          </div>
        </div>
        <div class="empty" v-else-if="!billingLoading">
          <b>当前期间无计费服务消费</b>
          <span>{{ periodCost.note || 'Always Free 免费配额内零费用运行' }}</span>
        </div>
      </div>

      <!-- 账单记录与在线下载 -->
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">账单开具记录与对账</h2>
          <div style="display:flex; align-items:center; gap:8px;">
            <span class="who" v-if="invoices.summary && invoices.summary.total">
              共 {{ invoices.summary.total }} 张账单
            </span>
            <el-button size="small" text bg @click="downloadInvoicesCsv" :disabled="!invoices.invoices || !invoices.invoices.length">
              导出账单报表 (CSV)
            </el-button>
          </div>
        </div>
        <template v-if="invoices.invoices">
          <div class="stats" style="margin-bottom:14px;" v-if="invoices.summary && invoices.summary.total">
            <div class="stat crit" v-if="invoices.summary.overdue">
              <div class="v">{{ invoices.summary.overdue }}</div><div class="k">已逾期</div>
            </div>
            <div class="stat"><div class="v">{{ invoices.summary.unpaid || 0 }}</div><div class="k">待支付</div></div>
            <div class="stat ok"><div class="v">{{ invoices.summary.paid || 0 }}</div><div class="k">已支付</div></div>
          </div>
          <div class="table-wrap" v-if="invoices.invoices.length">
            <el-table :data="invoices.invoices" size="small" stripe border>
              <el-table-column label="状态" width="104">
                <template #default="{row}">
                  <el-tag size="small" :type="row.state === 'paid' ? 'success' : (row.state === 'overdue' ? 'danger' : 'warning')">
                    {{ row.state === 'paid' ? '已付清' : (row.state === 'overdue' ? '已逾期' : '待支付') }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="账单号" min-width="160" show-overflow-tooltip>
                <template #default="{row}"><b style="color:#ffffff;">{{ row.number || row.invoice_id || '—' }}</b></template>
              </el-table-column>
              <el-table-column label="开票日期" width="120">
                <template #default="{row}"><span class="mono" style="color:#e2e8f0;">{{ fmtDay(row.time_invoice) }}</span></template>
              </el-table-column>
              <el-table-column label="到期日" width="120">
                <template #default="{row}"><span class="mono" style="color:#e2e8f0;">{{ fmtDay(row.time_due) }}</span></template>
              </el-table-column>
              <el-table-column label="开票金额" width="130">
                <template #default="{row}">
                  <span class="mono" style="color:#38bdf8; font-weight:700;">{{ row.currency }} {{ row.amount ?? '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="待付金额" width="130">
                <template #default="{row}">
                  <span class="mono" :style="row.state !== 'paid' && row.amount_due ? 'color:#fda4af; font-weight:700;' : 'color:#cbd5e1;'">
                    {{ row.amount_due ?? '—' }}
                  </span>
                </template>
              </el-table-column>
            </el-table>
          </div>
          <div class="empty" v-else-if="!billingLoading">
            <b>暂无计费账单</b>
            <span>Always Free 免费号不产生官方计费账单。</span>
          </div>
        </template>
      </div>
    </section>
  `,
  setup() {
    const billingLoading = ref(false);
    const selectedPeriod = ref('current_month');
    const periodCost = ref({});
    const invoices = ref({});

    async function loadPeriodCost() {
      if (!currentProfile.value) return;
      try {
        const { data } = await api.get('/api/monitor/period-cost', {
          params: { profile: currentProfile.value, period: selectedPeriod.value },
        });
        periodCost.value = data;
      } catch (e) {
        periodCost.value = { note: errMsg(e, '成本数据获取失败') };
      }
    }

    async function loadInvoices(force = false) {
      if (!currentProfile.value) return;
      try {
        const { data } = await api.get('/api/monitor/invoices', {
          params: { profile: currentProfile.value, refresh: !!force },
        });
        invoices.value = data;
      } catch (e) {
        invoices.value = { invoices: [] };
      }
    }

    async function loadBilling(force = false) {
      billingLoading.value = true;
      try {
        await Promise.all([loadPeriodCost(), loadInvoices(force)]);
      } finally {
        billingLoading.value = false;
      }
    }

    function downloadBlob(data, filename) {
      const blob = new Blob([data], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.setAttribute('href', url);
      link.setAttribute('download', filename);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    }

    async function downloadCostCsv() {
      if (!currentProfile.value) return;
      try {
        const { data } = await api.get('/api/monitor/export-cost-csv', {
          params: { profile: currentProfile.value, period: selectedPeriod.value },
        });
        downloadBlob(data, `cost_${currentProfile.value}_${selectedPeriod.value}.csv`);
        ElMessage?.success('成本明细 CSV 已导出');
      } catch (e) {
        ElMessage?.error(errMsg(e, '导出成本明细失败'));
      }
    }

    async function downloadInvoicesCsv() {
      if (!currentProfile.value) return;
      try {
        const { data } = await api.get('/api/monitor/export-invoices-csv', {
          params: { profile: currentProfile.value },
        });
        downloadBlob(data, `invoices_${currentProfile.value}.csv`);
        ElMessage?.success('账单报表 CSV 已导出');
      } catch (e) {
        ElMessage?.error(errMsg(e, '导出账单报表失败'));
      }
    }

    watch(currentProfile, () => loadBilling(false));
    onMounted(() => loadBilling(false));

    return {
      billingLoading,
      selectedPeriod,
      periodCost,
      invoices,
      loadPeriodCost,
      loadBilling,
      downloadCostCsv,
      downloadInvoicesCsv,
      fmtDay,
    };
  }
};
