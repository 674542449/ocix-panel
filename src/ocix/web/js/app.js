import api from './api.js';
import {
  token,
  user,
  appVersion,
  serviceOk,
  serviceHint,
  globalLoading,
  activeTab,
  lastUpdated,
  profiles,
  profilesLoading,
  currentProfile,
  lockedProfile,
  compartments,
  compsLoading,
  compartmentId,
  subtree,
  orphanBadge,
  loadProfiles,
  loadCompartments,
  loadLock,
} from './store.js';
import { NAV } from './constants.js';
import { OcixScene, errMsg } from './utils.js';

// 引入各 Tab 子组件
import InstancesTab from './tabs/InstancesTab.js';
import CreateTab from './tabs/CreateTab.js';
import RadarTab from './tabs/RadarTab.js';
import StorageTab from './tabs/StorageTab.js';
import FirewallTab from './tabs/FirewallTab.js';
import SshKeysTab from './tabs/SshKeysTab.js';
import UsageTab from './tabs/UsageTab.js';
import MetricsTab from './tabs/MetricsTab.js';
import BillingTab from './tabs/BillingTab.js';
import AuditTab from './tabs/AuditTab.js';
import ProfilesTab from './tabs/ProfilesTab.js';
import NotificationTab from './tabs/NotificationTab.js';
import PasswordTab from './tabs/PasswordTab.js';
import UpdateTab from './tabs/UpdateTab.js';

const { createApp, ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } = window.Vue;
const { ElMessage } = window.ElementPlus || {};

const App = {
  components: {
    InstancesTab,
    CreateTab,
    RadarTab,
    StorageTab,
    FirewallTab,
    SshKeysTab,
    UsageTab,
    MetricsTab,
    BillingTab,
    AuditTab,
    ProfilesTab,
    NotificationTab,
    PasswordTab,
    UpdateTab,
  },
  setup() {
    const loginForm = reactive({ username: '', password: '' });
    const loginLoading = ref(false);
    const loginErr = ref('');
    const autoRefresh = ref(false);
    const countdown = ref(30);
    const loginCanvas = ref(null);
    const loginFlow = ref(null);
    let autoTimer = null;

    const currentTabLabel = computed(() => {
      for (const g of NAV) {
        const hit = g.items.find((it) => it.name === activeTab.value);
        if (hit) return hit.label;
      }
      return 'OCIX';
    });

    function showFlow(from, to) {
      const el = loginFlow.value;
      if (!el) return;
      const span = (text, cls) => {
        const s = document.createElement('span');
        if (cls) s.className = cls;
        s.textContent = text;
        return s;
      };
      el.replaceChildren(span(from), span('→', 'arrow'), span(to));
    }

    function syncScene() {
      if (token.value) {
        OcixScene.stop();
        return;
      }
      nextTick(() => OcixScene.start(loginCanvas.value, showFlow));
    }

    async function doLogin() {
      if (!loginForm.username.trim() || !loginForm.password.trim()) {
        loginErr.value = '请输入用户名和密码';
        return;
      }
      loginLoading.value = true;
      loginErr.value = '';
      try {
        const formData = new FormData();
        formData.append('username', loginForm.username.trim());
        formData.append('password', loginForm.password);
        const { data } = await api.post('/api/auth/token', formData);
        token.value = data.access_token;
        user.value = loginForm.username.trim();
        localStorage.setItem('ocix_token', data.access_token);
        localStorage.setItem('ocix_user', user.value);
        loginForm.password = '';
        ElMessage?.success('登录成功');
        initApp();
      } catch (e) {
        loginErr.value = errMsg(e, '登录失败，请检查用户名或密码');
      } finally {
        loginLoading.value = false;
      }
    }

    function logout() {
      token.value = '';
      user.value = '';
      localStorage.removeItem('ocix_token');
      localStorage.removeItem('ocix_user');
      syncScene();
    }

    async function checkAuth() {
      if (!token.value) return;
      try {
        const { data } = await api.get('/api/auth/me');
        user.value = data.username;
        appVersion.value = data.version || '';
        serviceOk.value = true;
      } catch (e) {
        if (e.response && e.response.status === 401) {
          logout();
        } else {
          serviceOk.value = false;
          serviceHint.value = errMsg(e, '服务连接异常');
        }
      }
    }

    function onProfileChange(val) {
      localStorage.setItem('ocix_profile', val);
      loadCompartments();
    }

    function go(tabName) {
      activeTab.value = tabName;
    }

    function startAutoRefresh() {
      stopAutoRefresh();
      countdown.value = 30;
      autoTimer = setInterval(() => {
        countdown.value--;
        if (countdown.value <= 0) {
          countdown.value = 30;
          refreshActive();
        }
      }, 1000);
    }

    function stopAutoRefresh() {
      if (autoTimer) {
        clearInterval(autoTimer);
        autoTimer = null;
      }
    }

    function refreshActive() {
      const now = new Date();
      lastUpdated.value = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
      loadProfiles();
      loadCompartments();
    }

    async function initApp() {
      if (!token.value) {
        syncScene();
        return;
      }
      await checkAuth();
      await loadLock();
      await loadProfiles();
      await loadCompartments();
      refreshActive();
    }

    watch(token, syncScene);
    watch(autoRefresh, (v) => {
      if (v) startAutoRefresh();
      else stopAutoRefresh();
    });

    onMounted(initApp);
    onUnmounted(() => {
      OcixScene.stop();
      stopAutoRefresh();
    });

    return {
      NAV,
      token,
      user,
      appVersion,
      serviceOk,
      serviceHint,
      globalLoading,
      activeTab,
      lastUpdated,
      currentTabLabel,
      profiles,
      profilesLoading,
      currentProfile,
      lockedProfile,
      compartments,
      compsLoading,
      compartmentId,
      subtree,
      orphanBadge,
      loginForm,
      loginLoading,
      loginErr,
      loginCanvas,
      loginFlow,
      autoRefresh,
      countdown,
      doLogin,
      logout,
      onProfileChange,
      go,
      refreshActive,
    };
  },
};

// 启动应用
if (window.__ocixBoot) {
  window.__ocixBoot.then(() => {
    const app = createApp(App);
    if (window.ElementPlus) app.use(window.ElementPlus);
    app.mount('#app');
  });
} else {
  const app = createApp(App);
  if (window.ElementPlus) app.use(window.ElementPlus);
  app.mount('#app');
}
