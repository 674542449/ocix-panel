import api from '../api.js';
import { currentProfile, scopeParams, storage } from '../store.js';
import { fmtDate, errMsg } from '../utils.js';

const { ref, onMounted, watch } = window.Vue;
const { ElMessage, ElMessageBox } = window.ElementPlus || {};

export default {
  name: 'StorageTab',
  template: `
    <section class="pane">
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">卷与 200GB 额度</h2>
          <el-button size="small" @click="loadStorage" :loading="storageLoading">刷新</el-button>
        </div>

        <div v-if="storageLoading && !storage" aria-label="加载中">
          <div class="skel skel-row" v-for="i in 4" :key="i"></div>
        </div>
        <template v-else-if="storage">
          <div class="stats" style="margin-bottom:14px;">
            <div class="stat" :class="storage.summary.total_gb > storage.summary.limit_gb ? 'crit' : ''">
              <div class="v">{{ storage.summary.total_gb }}</div>
              <div class="k">已用 GB / {{ storage.summary.limit_gb }}</div>
            </div>
            <div class="stat"><div class="v">{{ storage.summary.boot_count }}</div><div class="k">引导卷</div></div>
            <div class="stat"><div class="v">{{ storage.summary.block_count }}</div><div class="k">块存储卷</div></div>
            <div class="stat" :class="storage.summary.orphan_count ? 'crit' : 'idle'">
              <div class="v">{{ storage.summary.orphan_gb }}</div>
              <div class="k">未挂载卷占用 GB</div>
            </div>
          </div>

          <el-alert v-if="storage.summary.orphan_count" type="warning" show-icon :closable="false"
                    style="margin-bottom:12px;"
                    :title="'检测到 ' + storage.summary.orphan_count + ' 个未挂载卷（' + storage.summary.orphan_gb + ' GB）'" />

          <div class="table-wrap" v-if="storage.volumes.length">
            <el-table :data="storage.volumes" size="small" stripe border style="width:100%;"
                      :row-class-name="volRowClass">
              <el-table-column label="挂载" width="150">
                <template #default="{row}">
                  <span class="state">
                    <i :class="['dot', row.orphan ? 'crit' : 'ok']"></i>
                    <span class="state-label">{{ row.orphan ? '未挂载' : (row.attached_to || '已挂载') }}</span>
                  </span>
                </template>
              </el-table-column>
              <el-table-column prop="display_name" label="名称" min-width="170" show-overflow-tooltip />
              <el-table-column label="类型" width="90">
                <template #default="{row}">{{ row.kind === 'boot' ? '引导卷' : '块存储' }}</template>
              </el-table-column>
              <el-table-column label="大小" width="90">
                <template #default="{row}"><span class="mono">{{ row.size_gb }} GB</span></template>
              </el-table-column>
              <el-table-column label="性能 (VPU/GB)" width="210">
                <template #default="{row}">
                  <div class="vpu-cell">
                    <el-input-number :model-value="row.vpus_per_gb" size="small"
                                     :min="vpuRange.min" :max="vpuRange.max" :step="vpuRange.step"
                                     step-strictly controls-position="right" style="width:110px;"
                                     @change="v => changePerformance(row, v)" />
                    <span class="vpu-tier" :class="{ paid: row.vpus_per_gb > vpuRange.free_max }">
                      {{ vpuTier(row.vpus_per_gb) }}
                    </span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="创建于" width="110">
                <template #default="{row}"><span class="mono">{{ fmtDate(row.time_created) }}</span></template>
              </el-table-column>
              <el-table-column label="操作" width="100" fixed="right">
                <template #default="{row}">
                  <el-button size="small" type="danger" :disabled="!row.orphan"
                             @click="deleteVolume(row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
          <div class="empty" v-else><b>当前账户下没有卷</b></div>
        </template>
        <div class="empty" v-else-if="!storageLoading"><b>暂无存储数据</b></div>
      </div>
    </section>
  `,
  setup() {
    const storageLoading = ref(false);
    const vpuRange = { min: 0, max: 120, step: 10, free_max: 10 };

    const volRowClass = ({ row }) => (row.orphan ? 'orphan-row' : '');
    const vpuTier = (v) => (v === 0 ? '低成本' : v <= 10 ? '均衡（免费）' : v <= 20 ? '更高性能' : '极致性能');

    async function loadStorage() {
      if (!currentProfile.value) return;
      storageLoading.value = true;
      try {
        const { data } = await api.get('/api/provision/storage', { params: scopeParams() });
        storage.value = data;
      } catch (e) {
        storage.value = null;
        ElMessage?.error(errMsg(e, '获取存储卷数据失败'));
      } finally {
        storageLoading.value = false;
      }
    }

    async function deleteVolume(row) {
      try {
        await ElMessageBox.confirm(`确认删除未挂载的「${row.display_name}」？此操作不可逆。`, '删除存储卷', {
          type: 'warning',
        });
      } catch {
        return;
      }
      try {
        await api.post('/api/provision/storage/delete', {
          profile: currentProfile.value,
          volume_id: row.id,
          kind: row.kind,
        });
        ElMessage?.success(`已删除卷 ${row.display_name}`);
        loadStorage();
      } catch (e) {
        ElMessage?.error(errMsg(e, '删除存储卷失败'));
      }
    }

    async function changePerformance(row, vpus) {
      try {
        await api.post('/api/provision/storage/performance', {
          profile: currentProfile.value,
          volume_id: row.id,
          kind: row.kind,
          vpus_per_gb: vpus,
        });
        ElMessage?.success(`${row.display_name} 性能调整成功`);
        loadStorage();
      } catch (e) {
        ElMessage?.error(errMsg(e, '调整性能失败'));
      }
    }

    watch(currentProfile, loadStorage);
    onMounted(loadStorage);

    return {
      storage,
      storageLoading,
      vpuRange,
      volRowClass,
      vpuTier,
      loadStorage,
      deleteVolume,
      changePerformance,
      fmtDate,
    };
  }
};
