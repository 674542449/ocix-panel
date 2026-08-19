import api from '../api.js';
import { errMsg } from '../utils.js';

const { ref, reactive, onMounted } = window.Vue;
const { ElMessage } = window.ElementPlus || {};

export default {
  name: 'NotificationTab',
  template: `
    <section class="pane">
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">Telegram 通知设置</h2>
          <div style="display:flex; align-items:center; gap:12px;">
            <span class="state" v-if="tgSettings.bot_token && tgSettings.chat_id">
              <i :class="['dot', tgSettings.enabled ? 'ok' : 'attn']"></i>
              <span class="state-label" style="font-weight:600;">{{ tgSettings.enabled ? '通知已生效' : '已暂停通知' }}</span>
            </span>
            <el-switch v-model="tgSettings.enabled" active-text="开启 TG 通知" />
          </div>
        </div>
        <el-form label-position="top" style="max-width:600px;">
          <el-form-item label="Telegram Bot Token">
            <el-input v-model="tgSettings.bot_token" placeholder="例如 123456789:ABCdefGhIjKlMnOpQrStUvWxYz" class="mono" clearable show-password />
          </el-form-item>
          <el-form-item label="接收通知的 Chat ID">
            <el-input v-model="tgSettings.chat_id" placeholder="例如 123456789 或群组 -100123456789" class="mono" clearable />
          </el-form-item>
          <div style="display:flex; gap:10px; margin-top:16px;">
            <el-button type="primary" :loading="tgSaving" @click="saveTgSettings">保存配置</el-button>
            <el-button :loading="tgTesting" @click="testTgConnection">发送测试消息</el-button>
          </div>
        </el-form>
      </div>
    </section>
  `,
  setup() {
    const tgSettings = reactive({ enabled: false, bot_token: '', chat_id: '', has_token: false });
    const tgLoading = ref(false);
    const tgSaving = ref(false);
    const tgTesting = ref(false);

    async function loadTgSettings() {
      tgLoading.value = true;
      try {
        const { data } = await api.get('/api/system/telegram');
        tgSettings.enabled = data.enabled || false;
        tgSettings.bot_token = data.bot_token || '';
        tgSettings.chat_id = data.chat_id || '';
        tgSettings.has_token = data.has_token || false;
      } catch (e) {
        ElMessage?.error(errMsg(e, '加载 Telegram 配置失败'));
      } finally {
        tgLoading.value = false;
      }
    }

    async function saveTgSettings() {
      if (tgSettings.bot_token && tgSettings.chat_id && !tgSettings.has_token) {
        tgSettings.enabled = true;
      }
      tgSaving.value = true;
      try {
        await api.post('/api/system/telegram', {
          enabled: tgSettings.enabled,
          bot_token: tgSettings.bot_token,
          chat_id: tgSettings.chat_id,
        });
        ElMessage?.success('Telegram 配置保存成功并已启用');
        loadTgSettings();
      } catch (e) {
        ElMessage?.error(errMsg(e, '保存失败'));
      } finally {
        tgSaving.value = false;
      }
    }

    async function testTgConnection() {
      if (!tgSettings.bot_token && !tgSettings.has_token) return ElMessage?.warning('请填写 Bot Token');
      if (!tgSettings.chat_id) return ElMessage?.warning('请填写 Chat ID');
      tgTesting.value = true;
      try {
        const { data } = await api.post('/api/system/telegram/test', {
          bot_token: tgSettings.bot_token,
          chat_id: tgSettings.chat_id,
        });
        ElMessage?.success(data.message || '测试消息发送成功！请检查 Telegram');
      } catch (e) {
        ElMessage?.error(errMsg(e, '测试发送失败，请检查 Token 与 Chat ID'));
      } finally {
        tgTesting.value = false;
      }
    }

    onMounted(loadTgSettings);

    return {
      tgSettings,
      tgLoading,
      tgSaving,
      tgTesting,
      loadTgSettings,
      saveTgSettings,
      testTgConnection,
    };
  }
};
