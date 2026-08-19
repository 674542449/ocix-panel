import api from '../api.js';
import { currentProfile, scopeParams, usage, storage } from '../store.js';
import { ACTION_LABEL } from '../constants.js';
import { copy, errMsg, fmtDate } from '../utils.js';

const { ref, reactive, computed, onMounted, watch } = window.Vue;
const { ElMessage, ElMessageBox } = window.ElementPlus || {};

export default {
  name: 'InstancesTab',
  template: `
    <section class="pane">
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">{{ currentProfile || '未选择账户' }} 的实例</h2>
        </div>

        <!-- 机队额度 -->
        <div class="fleet" v-if="instances.length || usage">
          <div class="meter">
            <div class="meter-head">
              <span class="meter-k">AMD 微型</span>
              <span class="meter-v">{{ fleet.amd }}<small>/2 台</small></span>
            </div>
            <div class="bar"><i :style="{ width: (fleet.amd / 2 * 100) + '%' }"></i></div>
          </div>
          <div class="meter">
            <div class="meter-head">
              <span class="meter-k">ARM OCPU</span>
              <span class="meter-v">{{ fleet.ocpu }}<small>/4</small></span>
            </div>
            <div class="bar"><i :style="{ width: (fleet.ocpu / 4 * 100) + '%' }"></i></div>
          </div>
          <div class="meter">
            <div class="meter-head">
              <span class="meter-k">ARM 内存</span>
              <span class="meter-v">{{ fleet.mem }}<small>/24 GB</small></span>
            </div>
            <div class="bar"><i :style="{ width: (fleet.mem / 24 * 100) + '%' }"></i></div>
          </div>
          <div class="meter">
            <div class="meter-head">
              <span class="meter-k">免费存储</span>
              <span class="meter-v">{{ storageUsedGb }}<small>/200 GB</small></span>
            </div>
            <div class="bar" :class="{ warn: typeof storageUsedGb === 'number' && storageUsedGb > 170 }">
              <i :style="{ width: (typeof storageUsedGb === 'number' ? Math.min(100, storageUsedGb / 200 * 100) : 0) + '%' }"></i>
            </div>
          </div>
        </div>

        <div class="bulkbar" v-if="instSelection.length">
          <span class="count">已选 {{ instSelection.length }} 台</span>
          <el-button size="small" type="success" @click="batchAct(instSelection,'START')">开机</el-button>
          <el-button size="small" @click="batchAct(instSelection,'SOFTSTOP')">关机</el-button>
          <el-button size="small" type="warning" @click="batchAct(instSelection,'SOFTRESET')">重启</el-button>
          <el-button size="small" text @click="clearInstSelection">取消选择</el-button>
        </div>

        <div v-if="loading && !instances.length" aria-label="加载中">
          <div class="skel skel-row" v-for="i in 4" :key="i"></div>
        </div>

        <!-- 实例列表表格 -->
        <div class="table-wrap" v-else-if="instances.length" :class="{ stale: loading }">
          <el-table ref="instTableRef" :data="instances" size="small" stripe border row-key="id"
                    @selection-change="instSelection = $event" style="width:100%;">
            <el-table-column type="selection" width="42" />
            <el-table-column label="状态" width="118">
              <template #default="{row}">
                <span class="state">
                  <i :class="['dot', stateTone(row.state)]"></i>
                  <span class="state-label">{{ stateText(row.state) }}</span>
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="display_name" label="名称" min-width="118" show-overflow-tooltip />
            <el-table-column label="规格" min-width="140">
              <template #default="{row}"><span class="mono">{{ specText(row) }}</span></template>
            </el-table-column>
            <el-table-column label="公网 IP" min-width="180">
              <template #default="{row}">
                <div class="ip-stack">
                  <span class="ip-cell">
                    <template v-if="row.public_ip">
                      <span class="mono">{{ row.public_ip }}</span>
                      <button class="copy-btn" @click="copy(row.public_ip)" :aria-label="'复制 ' + row.public_ip">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
                      </button>
                      <el-tooltip content="更换公网 IPv4 地址" placement="top">
                        <button class="mini-btn" @click="changeIp(row)" aria-label="更换公网 IP">更换</button>
                      </el-tooltip>
                    </template>
                    <span v-else class="mono" style="color:var(--text-3);">无公网 IP</span>
                  </span>
                  <span class="ip-sub mono" v-if="row.private_ip">内网 {{ row.private_ip }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="IPv6" min-width="150">
              <template #default="{row}">
                <span class="ip-cell" v-if="row.ipv6">
                  <span class="mono ip-trunc" :title="row.ipv6">{{ row.ipv6 }}</span>
                  <button class="copy-btn" @click="copy(row.ipv6)" :aria-label="'复制 ' + row.ipv6">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
                  </button>
                </span>
                <el-button v-else size="small" text type="primary" :loading="ipv6Busy === row.id"
                           @click="addIpv6(row)">+ 添加</el-button>
              </template>
            </el-table-column>
            <el-table-column label="创建时间" width="168">
              <template #default="{row}"><span class="mono">{{ fmtDate(row.time_created) }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="316" fixed="right">
              <template #default="{row}">
                <div class="row-actions">
                  <el-button size="small" type="primary" plain @click="openDetail(row)"
                             :loading="isBusy('detail:' + row.id)">详情</el-button>
                  <el-button size="small" type="success" :disabled="!canStart(row)" @click="act(row,'START')">开机</el-button>
                  <el-button size="small" :disabled="!canStop(row)" @click="act(row,'SOFTSTOP')">关机</el-button>
                  <el-button size="small" type="warning" :disabled="!canReset(row)" @click="act(row,'SOFTRESET')">重启</el-button>
                  <el-button size="small" text class="term-btn" @click="terminate(row)">终止</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <div class="empty" v-else-if="!loading">
          <b>当前账户下没有实例</b>
        </div>
      </div>

      <!-- 详情抽屉 -->
      <el-drawer v-model="detailOpen" :title="detail.display_name || '实例详情'" size="560px" direction="rtl">
        <div v-if="detailLoading" aria-label="加载中">
          <div class="skel skel-row" v-for="i in 6" :key="i"></div>
        </div>
        <template v-else-if="detail.id">
          <dl class="dl">
            <dt>状态</dt>
            <dd>
              <span class="state">
                <i :class="['dot', stateTone(detail.state)]"></i>
                <span class="state-label">{{ stateText(detail.state) }}</span>
              </span>
            </dd>
            <dt>规格</dt>
            <dd class="mono">{{ detail.shape }}
              <template v-if="detail.ocpus">· {{ detail.ocpus }} OCPU · {{ detail.memory_gb }} GB</template>
            </dd>
            <dt>公网 IP</dt>
            <dd class="mono">{{ detail.public_ip || '—' }}</dd>
            <dt>内网 IP</dt>
            <dd class="mono">{{ detail.private_ip || '—' }}</dd>
            <dt>IPv6</dt>
            <dd class="mono">{{ detail.ipv6 || '未分配' }}</dd>
            <dt>可用域</dt>
            <dd class="mono">{{ detail.availability_domain || '—' }}</dd>
            <dt>创建于</dt>
            <dd class="mono">{{ fmtDate(detail.time_created) }}</dd>
            <dt>OCID</dt>
            <dd class="mono" style="font-size:11px;">{{ detail.id }}</dd>
          </dl>

          <!-- 网页终端 -->
          <div class="drawer-sec">
            <h3>网页终端</h3>
            <span class="who" style="font-size:12px;">支持直连 SSH 与串口控制台</span>
          </div>

          <!-- 改规格 -->
          <div class="drawer-sec" v-if="detail.is_flex">
            <h3>调整规格</h3>
            <div style="display:flex; gap:10px; align-items:flex-end; flex-wrap:wrap;">
              <div>
                <div class="alloc-k" style="margin-bottom:4px;">OCPU</div>
                <el-input-number v-model="resizeForm.ocpus" :min="1" :max="4" :step="1"
                                 style="width:120px;" @change="syncResizeMem" />
              </div>
              <div>
                <div class="alloc-k" style="margin-bottom:4px;">内存 GB</div>
                <el-input-number v-model="resizeForm.memory_gb" :min="1"
                                 :max="resizeForm.ocpus * 6" :step="1" style="width:120px;" />
              </div>
              <el-button type="primary" @click="doResize" :loading="resizing">应用</el-button>
            </div>
          </div>

          <!-- 引导卷 -->
          <div class="drawer-sec" v-if="detail.boot_volume">
            <h3>
              引导卷
              <span class="who mono">{{ detail.boot_volume.size_gb }} GB</span>
            </h3>
            <div style="display:flex; gap:10px; align-items:flex-end; flex-wrap:wrap;">
              <div>
                <div class="alloc-k" style="margin-bottom:4px;">扩容到 GB</div>
                <el-input-number v-model="growForm.size_gb"
                                 :min="detail.boot_volume.size_gb" :max="200" :step="10"
                                 style="width:130px;" />
              </div>
              <el-button @click="doGrow" :loading="growing">扩容</el-button>
            </div>
          </div>

          <!-- 备份 -->
          <div class="drawer-sec" v-if="detail.boot_volume">
            <h3>引导卷备份</h3>
            <span class="who" style="font-size:12px;">备份管理与快照还原</span>
          </div>
        </template>
      </el-drawer>
    </section>
  `,
  setup() {
    const instances = ref([]);
    const loading = ref(false);
    const instSelection = ref([]);
    const instTableRef = ref(null);
    const busyKeys = ref(new Set());
    const ipv6Busy = ref(null);

    const detailOpen = ref(false);
    const detail = ref({});
    const detailLoading = ref(false);
    const resizeForm = reactive({ ocpus: 1, memory_gb: 6 });
    const resizing = ref(false);
    const growForm = reactive({ size_gb: 50 });
    const growing = ref(false);

    const isBusy = (k) => busyKeys.value.has(k);
    const setBusy = (k, v) => {
      const next = new Set(busyKeys.value);
      if (v) next.add(k); else next.delete(k);
      busyKeys.value = next;
    };

    const fleet = computed(() => {
      let amd = 0, ocpu = 0, mem = 0;
      for (const i of instances.value) {
        if (i.state === 'TERMINATED') continue;
        if (i.shape && i.shape.includes('Micro')) {
          amd += 1;
        } else if (i.shape && i.shape.includes('A1.Flex')) {
          ocpu += i.ocpus || 0;
          mem += i.memory_gb || 0;
        }
      }
      return { amd, ocpu, mem };
    });

    const storageUsedGb = computed(() => {
      if (storage.value && storage.value.summary) return storage.value.summary.total_gb;
      return '—';
    });

    const stateTone = (st) => {
      if (st === 'RUNNING') return 'ok';
      if (st === 'STOPPED') return 'idle';
      if (st === 'TERMINATING' || st === 'TERMINATED') return 'crit';
      return 'warn';
    };

    const stateText = (st) => {
      const m = { RUNNING: '运行中', STOPPED: '已停止', STARTING: '正在启动', STOPPING: '正在停止', TERMINATING: '正在终止', TERMINATED: '已终止', PROVISIONING: '正在创建' };
      return m[st] || st;
    };

    const specText = (row) => {
      if (row.shape && row.shape.includes('Micro')) return 'AMD · 1C/1G';
      if (row.shape && row.shape.includes('A1.Flex')) return `ARM · ${row.ocpus || 1}C/${row.memory_gb || 6}G`;
      return row.shape || '—';
    };

    const canStart = (row) => row.state === 'STOPPED';
    const canStop = (row) => row.state === 'RUNNING';
    const canReset = (row) => row.state === 'RUNNING';

    async function loadInstances() {
      if (!currentProfile.value) return;
      loading.value = true;
      try {
        const { data } = await api.get('/api/instances', { params: scopeParams() });
        instances.value = data.instances || [];
      } catch (e) {
        ElMessage?.error(errMsg(e, '加载实例失败'));
      } finally {
        loading.value = false;
      }
    }

    async function act(row, action) {
      const name = ACTION_LABEL[action] || action;
      try {
        await ElMessageBox.confirm(`确认对「${row.display_name}」执行${name}操作？`, `${name}实例`, { type: 'warning' });
      } catch {
        return;
      }
      try {
        await api.post(`/api/instances/${row.id}/action`, {
          profile: currentProfile.value,
          action,
        });
        ElMessage?.success(`已下发${name}指令`);
        loadInstances();
      } catch (e) {
        ElMessage?.error(errMsg(e, `${name}失败`));
      }
    }

    async function batchAct(rows, action) {
      const name = ACTION_LABEL[action] || action;
      try {
        await ElMessageBox.confirm(`确认对所选 ${rows.length} 台实例执行${name}操作？`, `批量${name}`, { type: 'warning' });
      } catch {
        return;
      }
      for (const r of rows) {
        try {
          await api.post(`/api/instances/${r.id}/action`, { profile: currentProfile.value, action });
        } catch {}
      }
      ElMessage?.success(`批量${name}指令已下发`);
      clearInstSelection();
      loadInstances();
    }

    function clearInstSelection() {
      instSelection.value = [];
      if (instTableRef.value) instTableRef.value.clearSelection();
    }

    async function terminate(row) {
      try {
        await ElMessageBox.confirm(`确认彻底终止（删除）实例「${row.display_name}」？此操作不可逆！`, '终止实例', { type: 'error' });
        await api.post(`/api/instances/${row.id}/terminate`, { profile: currentProfile.value });
        ElMessage?.success('已发起终止操作');
        loadInstances();
      } catch {}
    }

    async function changeIp(row) {
      try {
        await ElMessageBox.confirm(`确认更换「${row.display_name}」的公网 IP 吗？`, '更换公网 IP', { type: 'warning' });
        await api.post(`/api/instances/${row.id}/change-ip`, { profile: currentProfile.value });
        ElMessage?.success('公网 IP 更换成功');
        loadInstances();
      } catch (e) {
        ElMessage?.error(errMsg(e, '更换 IP 失败'));
      }
    }

    async function addIpv6(row) {
      ipv6Busy.value = row.id;
      try {
        await api.post(`/api/instances/${row.id}/assign-ipv6`, { profile: currentProfile.value });
        ElMessage?.success('IPv6 地址分配成功');
        loadInstances();
      } catch (e) {
        ElMessage?.error(errMsg(e, '分配 IPv6 失败'));
      } finally {
        ipv6Busy.value = null;
      }
    }

    async function openDetail(row) {
      detailOpen.value = true;
      detailLoading.value = true;
      detail.value = {};
      try {
        const { data } = await api.get(`/api/instances/${row.id}`, {
          params: { profile: currentProfile.value },
        });
        detail.value = data;
        resizeForm.ocpus = data.ocpus || 1;
        resizeForm.memory_gb = data.memory_gb || 6;
        if (data.boot_volume) {
          growForm.size_gb = data.boot_volume.size_gb || 50;
        }
      } catch (e) {
        ElMessage?.error(errMsg(e, '获取实例详情失败'));
      } finally {
        detailLoading.value = false;
      }
    }

    function syncResizeMem() {
      resizeForm.memory_gb = Math.min(resizeForm.ocpus * 6, Math.max(1, resizeForm.memory_gb));
    }

    async function doResize() {
      resizing.value = true;
      try {
        await api.post(`/api/instances/${detail.value.id}/resize`, {
          profile: currentProfile.value,
          ocpus: resizeForm.ocpus,
          memory_gb: resizeForm.memory_gb,
        });
        ElMessage?.success('规格调整成功');
        openDetail(detail.value);
        loadInstances();
      } catch (e) {
        ElMessage?.error(errMsg(e, '调整规格失败'));
      } finally {
        resizing.value = false;
      }
    }

    async function doGrow() {
      growing.value = true;
      try {
        await api.post(`/api/instances/${detail.value.id}/grow-boot-volume`, {
          profile: currentProfile.value,
          size_gb: growForm.size_gb,
        });
        ElMessage?.success('引导卷扩容成功');
        openDetail(detail.value);
      } catch (e) {
        ElMessage?.error(errMsg(e, '扩容失败'));
      } finally {
        growing.value = false;
      }
    }

    watch(currentProfile, loadInstances);
    onMounted(loadInstances);

    return {
      instances,
      loading,
      instSelection,
      instTableRef,
      fleet,
      storageUsedGb,
      detailOpen,
      detail,
      detailLoading,
      resizeForm,
      resizing,
      growForm,
      growing,
      ipv6Busy,
      currentProfile,
      usage,
      isBusy,
      stateTone,
      stateText,
      specText,
      canStart,
      canStop,
      canReset,
      loadInstances,
      act,
      batchAct,
      clearInstSelection,
      terminate,
      changeIp,
      addIpv6,
      openDetail,
      syncResizeMem,
      doResize,
      doGrow,
      copy,
      fmtDate,
    };
  }
};
