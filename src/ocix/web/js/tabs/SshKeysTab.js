import api from '../api.js';
import { fmtDate, copy, errMsg } from '../utils.js';

const { ref, reactive, computed, onMounted } = window.Vue;
const { ElMessage, ElMessageBox } = window.ElementPlus || {};

export default {
  name: 'SshKeysTab',
  template: `
    <section class="pane">
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">SSH 公钥池</h2>
          <el-button size="small" type="primary" @click="openCreateKeyModal">+ 添加 SSH 公钥</el-button>
        </div>
        <div class="table-wrap" v-if="sshKeys.length">
          <el-table :data="sshKeys" size="small" stripe border style="width:100%;">
            <el-table-column prop="name" label="备注名称" min-width="150" />
            <el-table-column prop="key_type" label="算法类型" width="110">
              <template #default="{row}">
                <el-tag size="small" type="success">{{ row.key_type || 'SSH' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="fingerprint" label="SHA256 指纹" min-width="210">
              <template #default="{row}">
                <span class="mono" style="font-size:11.5px;">{{ row.fingerprint || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="公钥预览" min-width="240">
              <template #default="{row}">
                <span class="mono" style="color:var(--text-3); font-size:11.5px;">
                  {{ row.public_key.slice(0, 30) }}...{{ row.public_key.slice(-14) }}
                </span>
                <button class="copy-btn" @click="copy(row.public_key)" title="复制完整公钥">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
                </button>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="150">
              <template #default="{row}"><span class="mono">{{ fmtDate(row.created_at) }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{row}">
                <el-button size="small" text type="primary" @click="openEditKeyModal(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="deleteSshKey(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <div class="empty" v-else-if="!sshKeysLoading">
          <b>公钥池暂无密钥</b>
        </div>
      </div>

      <!-- 添加/编辑公钥弹窗 -->
      <el-dialog v-model="keyModalOpen" :title="keyModalTitle" width="560px" destroy-on-close>
        <el-form label-position="top">
          <el-form-item label="公钥备注名称" required>
            <el-input v-model="keyForm.name" placeholder="例如 My-MacBook-Pro 或 main-key" />
          </el-form-item>
          <el-form-item label="SSH 公钥内容（支持 ssh-rsa / ssh-ed25519 / ecdsa-sha2）" required>
            <el-input v-model="keyForm.public_key" type="textarea" :rows="5" placeholder="以 ssh-rsa / ssh-ed25519 开头的公钥文本" class="mono" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="keyModalOpen = false">取消</el-button>
          <el-button type="primary" :loading="keySaving" @click="saveSshKey">保存</el-button>
        </template>
      </el-dialog>
    </section>
  `,
  setup() {
    const sshKeys = ref([]);
    const sshKeysLoading = ref(false);
    const keyForm = reactive({ id: null, name: '', public_key: '' });
    const keyModalOpen = ref(false);
    const keySaving = ref(false);
    const keyModalTitle = computed(() => keyForm.id ? '编辑 SSH 公钥' : '添加 SSH 公钥');

    async function loadSshKeys() {
      sshKeysLoading.value = true;
      try {
        const { data } = await api.get('/api/ssh-keys');
        sshKeys.value = data.keys || [];
      } catch (e) {
        ElMessage?.error(errMsg(e, '加载公钥列表失败'));
      } finally {
        sshKeysLoading.value = false;
      }
    }

    function openCreateKeyModal() {
      keyForm.id = null;
      keyForm.name = '';
      keyForm.public_key = '';
      keyModalOpen.value = true;
    }

    function openEditKeyModal(k) {
      keyForm.id = k.id;
      keyForm.name = k.name;
      keyForm.public_key = k.public_key;
      keyModalOpen.value = true;
    }

    async function saveSshKey() {
      if (!keyForm.name.trim()) return ElMessage?.warning('请输入公钥备注名称');
      if (!keyForm.public_key.trim()) return ElMessage?.warning('请输入公钥内容');
      keySaving.value = true;
      try {
        if (keyForm.id) {
          await api.put(`/api/ssh-keys/${keyForm.id}`, { name: keyForm.name, public_key: keyForm.public_key });
          ElMessage?.success('公钥更新成功');
        } else {
          await api.post('/api/ssh-keys', { name: keyForm.name, public_key: keyForm.public_key });
          ElMessage?.success('公钥添加成功');
        }
        keyModalOpen.value = false;
        loadSshKeys();
      } catch (e) {
        ElMessage?.error(errMsg(e, '保存公钥失败'));
      } finally {
        keySaving.value = false;
      }
    }

    async function deleteSshKey(k) {
      try {
        await ElMessageBox.confirm(`确认删除公钥「${k.name}」吗？`, '删除公钥', { type: 'warning' });
        await api.delete(`/api/ssh-keys/${k.id}`);
        ElMessage?.success('公钥已删除');
        loadSshKeys();
      } catch {}
    }

    onMounted(loadSshKeys);

    return {
      sshKeys,
      sshKeysLoading,
      keyForm,
      keyModalOpen,
      keySaving,
      keyModalTitle,
      loadSshKeys,
      openCreateKeyModal,
      openEditKeyModal,
      saveSshKey,
      deleteSshKey,
      fmtDate,
      copy,
    };
  }
};
