import api from '../api.js';
import { copy, errMsg } from '../utils.js';

const { ref, computed, onMounted, onUnmounted } = window.Vue;
const { ElMessage } = window.ElementPlus || {};

export default {
  name: 'UpdateTab',
  template: `
    <section class="pane">
      <div class="card" style="max-width:720px;">
        <div class="card-head">
          <h2 class="card-title">版本与更新</h2>
          <el-button size="small" @click="loadSystem(true)" :loading="sysLoading">检查更新</el-button>
        </div>

        <div class="ver-row">
          <div class="ver-box">
            <div class="k">当前版本</div>
            <div class="v mono">v{{ sys.current || '—' }}</div>
          </div>
          <div class="ver-arrow" v-if="sys.update_available">→</div>
          <div class="ver-box" v-if="sys.update_available">
            <div class="k">最新版本</div>
            <div class="v mono" style="color:var(--ok);">v{{ sys.latest }}</div>
          </div>
        </div>

        <el-alert v-if="sys.check_error" type="info" show-icon :closable="false"
                  style="margin-bottom:12px;" title="获取最新版本信息异常"
                  :description="sys.check_error" />
        <el-alert v-else-if="sys.update_available" type="warning" show-icon :closable="false"
                  style="margin-bottom:12px;"
                  :title="'检测到新版本 v' + sys.latest" />
        <el-alert v-else-if="sys.latest" type="success" show-icon :closable="false"
                  style="margin-bottom:12px;" title="当前已是最新版本" />

        <div class="card-head" style="margin-top:18px;">
          <h2 class="card-title">一键更新</h2>
        </div>

        <template v-if="upd.agent && !upd.agent.online">
          <el-alert type="warning" show-icon :closable="false" style="margin-bottom:10px;"
                    title="宿主机更新代理未运行"
                    :description="upd.agent.hint" />
          <div class="cmd-box">
            <code class="mono">{{ upd.agent.fix_command }}</code>
            <button class="copy-btn" @click="copy(upd.agent.fix_command)" aria-label="复制命令">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
            </button>
          </div>
        </template>

        <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-top:12px;">
          <el-button type="primary" @click="doUpdate"
                     :loading="updBusy"
                     :disabled="updRunning || (upd.agent && !upd.agent.online)">
            {{ updRunning ? '更新中…' : '立即更新' }}
          </el-button>
          <el-tag v-if="upd.state && upd.state !== 'idle'" :type="updTagType" size="small">
            {{ updStateText }}
          </el-tag>
          <span class="who" v-if="upd.message">{{ upd.message }}</span>
        </div>

        <div v-if="upd.log" class="log-box">
          <pre class="mono">{{ upd.log }}</pre>
        </div>

        <div class="card-head" style="margin-top:18px;">
          <h2 class="card-title">手动更新指令</h2>
        </div>
        <div class="cmd-box">
          <code class="mono">{{ sys.update_command || 'cd /opt/ocix && git pull && bash scripts/update.sh' }}</code>
          <button class="copy-btn" @click="copy(sys.update_command || 'cd /opt/ocix && git pull && bash scripts/update.sh')" aria-label="复制更新命令">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
          </button>
        </div>
      </div>
    </section>
  `,
  setup() {
    const sys = ref({});
    const sysLoading = ref(false);
    const upd = ref({});
    const updBusy = ref(false);
    let timer = null;

    const updRunning = computed(() => ['pending', 'running'].includes(upd.value.state));
    const updTagType = computed(() => {
      const s = upd.value.state;
      if (s === 'done') return 'success';
      if (s === 'failed') return 'danger';
      if (s === 'running' || s === 'pending') return 'warning';
      return 'info';
    });
    const updStateText = computed(() => {
      const s = upd.value.state;
      if (s === 'done') return '更新完成';
      if (s === 'failed') return '更新失败';
      if (s === 'running') return '正在更新';
      if (s === 'pending') return '等待执行';
      return s || '就绪';
    });

    async function loadSystem(force = false) {
      sysLoading.value = true;
      try {
        const { data } = await api.get('/api/system/info', { params: { refresh: !!force } });
        sys.value = data;
        if (force) {
          ElMessage?.[data.update_available ? 'warning' : 'success'](
            data.update_available ? `有新版本 v${data.latest}` : '已经是最新版本'
          );
        }
      } catch (e) {
        ElMessage?.error(errMsg(e, '版本信息获取失败'));
      } finally {
        sysLoading.value = false;
      }
    }

    async function loadUpdateStatus() {
      try {
        const { data } = await api.get('/api/system/update/status');
        upd.value = data;
      } catch {}
    }

    async function doUpdate() {
      updBusy.value = true;
      try {
        await api.post('/api/system/update');
        ElMessage?.success('更新请求已提交');
        loadUpdateStatus();
        startWatch();
      } catch (e) {
        ElMessage?.error(errMsg(e, '发起更新失败'));
      } finally {
        updBusy.value = false;
      }
    }

    function startWatch() {
      stopWatch();
      timer = setInterval(async () => {
        await loadUpdateStatus();
        if (!updRunning.value) stopWatch();
      }, 3000);
    }

    function stopWatch() {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
    }

    onMounted(async () => {
      await loadSystem(false);
      await loadUpdateStatus();
      if (updRunning.value) startWatch();
    });

    onUnmounted(stopWatch);

    return {
      sys,
      sysLoading,
      upd,
      updBusy,
      updRunning,
      updTagType,
      updStateText,
      loadSystem,
      doUpdate,
      copy,
    };
  }
};
