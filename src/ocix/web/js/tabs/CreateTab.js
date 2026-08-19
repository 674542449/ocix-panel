import api from '../api.js';
import { currentProfile, scopeParams } from '../store.js';
import { randomName } from '../constants.js';
import { copy, errMsg } from '../utils.js';

const { ref, reactive, computed, onMounted, watch } = window.Vue;
const { ElMessage } = window.ElementPlus || {};

export default {
  name: 'CreateTab',
  template: `
    <section class="pane">
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">新建实例</h2>
        </div>

        <div class="create-grid">
          <div>
            <el-form label-position="top">
              <el-form-item label="实例名称">
                <div style="display:flex; gap:8px; width:100%;">
                  <el-input v-model="createForm.display_name" placeholder="例如 web-prod-01" style="flex:1;" />
                  <el-button @click="createForm.display_name = randomName()">随机</el-button>
                </div>
              </el-form-item>

              <el-form-item label="实例架构与规格">
                <div class="seg" role="group" aria-label="架构">
                  <button type="button" :class="{ on: isArm }" @click="setShape('VM.Standard.A1.Flex')">
                    ARM 架构 (Ampere A1.Flex)
                  </button>
                  <button type="button" :class="{ on: !isArm }" @click="setShape('VM.Standard.E2.1.Micro')">
                    AMD 架构 (E2.1.Micro)
                  </button>
                </div>
              </el-form-item>

              <template v-if="isArm">
                <el-form-item :label="'OCPU：' + createForm.ocpus + ' 核'">
                  <el-slider v-model="createForm.ocpus" :min="1" :max="4" :step="1" show-stops @change="onOcpuChange" style="width:100%;" />
                </el-form-item>
                <el-form-item :label="'内存：' + createForm.memory_gb + ' GB'">
                  <el-slider v-model="createForm.memory_gb" :min="1" :max="24" :step="1" @change="runPreflight" style="width:100%;" />
                </el-form-item>
              </template>
              <el-form-item v-else label="规格配置">
                <span class="mono" style="color:var(--text-2);">1 OCPU / 1 GB (固定配置)</span>
              </el-form-item>

              <el-form-item :label="'引导卷：' + createForm.boot_gb + ' GB'">
                <el-slider v-model="createForm.boot_gb" :min="50" :max="200" :step="5" @change="runPreflight" style="width:100%;" />
              </el-form-item>

              <el-form-item label="可用域 (AD 域)">
                <div style="width:100%;">
                  <el-select v-model="createForm.availability_domain" style="width:100%;" filterable placeholder="选择可用域">
                    <el-option v-for="ad in opts.availability_domains" :key="ad" :value="ad" :label="ad" />
                  </el-select>
                  <div v-if="opts.availability_domains && opts.availability_domains.length > 1"
                       style="margin-top:6px; display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
                    <span style="font-size:11.5px; color:var(--text-3);">切换可用域：</span>
                    <el-button v-for="it in opts.availability_domains" :key="it" size="small"
                               :type="createForm.availability_domain === it ? 'primary' : 'default'"
                               text bg @click="createForm.availability_domain = it">
                      {{ it.split('-').slice(-2).join('-') || it }}
                    </el-button>
                  </div>
                </div>
              </el-form-item>

              <el-form-item label="故障域 (Fault Domain)">
                <el-select v-model="createForm.fault_domain" style="width:100%;" placeholder="自动选择 (智能推荐)">
                  <el-option value="" label="自动选择 (智能推荐有库存的故障域)" />
                  <el-option value="FAULT-DOMAIN-1" label="FAULT-DOMAIN-1 (故障域 1)" />
                  <el-option value="FAULT-DOMAIN-2" label="FAULT-DOMAIN-2 (故障域 2)" />
                  <el-option value="FAULT-DOMAIN-3" label="FAULT-DOMAIN-3 (故障域 3)" />
                </el-select>
              </el-form-item>

              <el-form-item label="系统镜像">
                <el-select v-model="createForm.image_id" style="width:100%;" filterable :loading="optsLoading" placeholder="选择镜像">
                  <el-option v-for="im in opts.images" :key="im.id" :value="im.id" :label="im.display_name" />
                </el-select>
              </el-form-item>

              <el-form-item label="网络">
                <div style="width:100%;">
                  <el-checkbox v-model="createForm.assign_ipv6" label="分配 IPv6 地址" />
                  <el-checkbox v-model="createForm.open_all_ports" label="放行所有端口" />
                </div>
              </el-form-item>

              <el-form-item label="智能容量探测与防封号抢机">
                <div style="width:100%;">
                  <el-checkbox v-model="createForm.capacity_probe" label="智能容量预检（先探测 OCI 放货状态再下单，避免 429 报错与封号）" />
                  <div v-if="createForm.capacity_probe" style="margin-top:6px;">
                    <el-checkbox v-model="createForm.auto_retry_until_available" label="无库存时自动抢机（智能低频探测直到放货自动下单）" />
                    <div v-if="createForm.auto_retry_until_available" style="margin-top:6px; display:flex; align-items:center; gap:8px;">
                      <span style="font-size:12px; color:var(--text-3);">最大抢机时长：</span>
                      <el-select v-model="createForm.max_retry_minutes" size="small" style="width:110px;">
                        <el-option :value="30" label="30 分钟" />
                        <el-option :value="60" label="1 小时" />
                        <el-option :value="180" label="3 小时" />
                        <el-option :value="360" label="6 小时" />
                      </el-select>
                    </div>
                  </div>
                </div>
              </el-form-item>

              <el-form-item label="登录方式">
                <div style="width:100%;">
                  <el-checkbox v-model="createForm.use_password" label="启用 root 密码登录" />
                  <div v-if="createForm.use_password" style="margin-top:8px;">
                    <div style="display:flex; gap:8px; align-items:center;">
                      <el-input v-model="createForm.root_password" class="mono" placeholder="至少 12 位" show-password style="flex:1;" />
                      <el-button size="small" @click="rollRootPassword">随机生成</el-button>
                      <el-button size="small" :disabled="!createForm.root_password" @click="copy(createForm.root_password)">复制</el-button>
                    </div>
                  </div>
                </div>
              </el-form-item>

              <el-form-item label="SSH 公钥">
                <div style="width:100%;">
                  <div style="display:flex; gap:8px; margin-bottom:8px; align-items:center;">
                    <el-select v-model="selectedKeyId" placeholder="从公钥池快速选择" style="flex:1;" clearable @change="onSelectKeyFromVault">
                      <el-option v-for="k in sshKeys" :key="k.id" :value="k.id" :label="k.name + ' (' + (k.key_type || 'SSH') + ')'" />
                    </el-select>
                    <el-button size="small" @click="$refs.pubkeyInput.click()">从文件导入</el-button>
                    <input ref="pubkeyInput" type="file" accept=".pub,.txt,text/plain" style="display:none;" @change="onPubKeyFile" />
                  </div>
                  <el-input v-model="createForm.ssh_public_key" type="textarea" :rows="3" class="mono"
                            placeholder="ssh-ed25519 AAAA... 或 ssh-rsa AAAA..." />
                </div>
              </el-form-item>
            </el-form>
          </div>

          <!-- 额度预检 -->
          <div>
            <div class="pf-card" :class="preflight && preflight.allow ? 'ok' : (preflight ? 'bad' : '')">
              <div class="pf-head">
                <h2 class="card-title">额度预检</h2>
                <span class="state" v-if="preflight">
                  <i :class="['dot', preflight.allow ? 'ok' : 'crit']"></i>
                  <span class="state-label">{{ preflight.allow ? '可以创建' : '超出额度' }}</span>
                </span>
                <span class="who" v-else-if="preflightLoading">核算中…</span>
              </div>

              <template v-if="preflight">
                <div class="pf-row" v-for="c in (preflight.checks || [])" :key="c.key">
                  <i :class="['dot', c.ok ? 'ok' : 'crit']"></i>
                  <span class="pf-label">{{ c.label }}</span>
                  <span class="pf-calc mono">
                    {{ c.current }} <em>+{{ c.adding }}</em> = <b>{{ c.after }}</b> / {{ c.limit }}{{ c.unit }}
                  </span>
                </div>

                <div class="pf-msg crit" v-for="(b,i) in (preflight.blockers || [])" :key="'b'+i">{{ b }}</div>
                <div class="pf-msg warn" v-for="(w,i) in (preflight.warnings || [])" :key="'w'+i">{{ w }}</div>
              </template>
              <div class="empty" v-else-if="!preflightLoading" style="padding:20px 8px;">
                填好规格后自动核算
              </div>

              <el-button type="primary" size="large" style="width:100%; margin-top:14px;"
                         :disabled="!canCreate" :loading="creating" @click="createInstance">
                {{ preflight && !preflight.allow ? '超出免费额度，无法创建' : (createForm.auto_retry_until_available ? '开启智能抢机' : '创建实例') }}
              </el-button>
              <p class="create-step" v-if="creating && createStep">
                <i class="dot warn"></i><span>{{ createStep }}</span>
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  `,
  setup(props, { emit }) {
    const opts = ref({ availability_domains: [], images: [], subnets: [] });
    const optsLoading = ref(false);
    const preflight = ref(null);
    const preflightLoading = ref(false);
    const creating = ref(false);
    const createStep = ref('');
    const selectedKeyId = ref(null);
    const sshKeys = ref([]);

    const createForm = reactive({
      display_name: randomName(),
      shape: 'VM.Standard.A1.Flex',
      ocpus: 4,
      memory_gb: 24,
      boot_gb: 50,
      availability_domain: '',
      fault_domain: '',
      image_id: '',
      assign_ipv6: false,
      open_all_ports: true,
      capacity_probe: true,
      auto_retry_until_available: false,
      max_retry_minutes: 60,
      use_password: true,
      root_password: '',
      ssh_public_key: '',
    });

    const isArm = computed(() => createForm.shape.includes('A1.Flex'));
    const canCreate = computed(() =>
      !!(createForm.availability_domain && createForm.image_id && (preflight.value ? preflight.value.allow : true))
    );

    function setShape(s) {
      createForm.shape = s;
      if (s.includes('A1.Flex')) {
        createForm.ocpus = 4;
        createForm.memory_gb = 24;
      } else {
        createForm.ocpus = 1;
        createForm.memory_gb = 1;
      }
      runPreflight();
    }

    function onOcpuChange(v) {
      createForm.memory_gb = Math.min(24, Math.max(v * 1, v * 6));
      runPreflight();
    }

    function rollRootPassword() {
      const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789!@#$%^&*';
      let pw = '';
      for (let i = 0; i < 16; i++) {
        pw += chars.charAt(Math.floor(Math.random() * chars.length));
      }
      createForm.root_password = pw;
    }

    async function loadSshKeys() {
      try {
        const { data } = await api.get('/api/ssh-keys');
        sshKeys.value = data.keys || [];
      } catch {}
    }

    function onSelectKeyFromVault(id) {
      const hit = sshKeys.value.find((k) => k.id === id);
      if (hit) createForm.ssh_public_key = hit.public_key;
    }

    function onPubKeyFile(e) {
      const f = e.target.files && e.target.files[0];
      if (!f) return;
      const reader = new FileReader();
      reader.onload = (evt) => {
        createForm.ssh_public_key = evt.target.result;
      };
      reader.readAsText(f);
    }

    async function loadCreateOptions() {
      if (!currentProfile.value) return;
      optsLoading.value = true;
      try {
        const { data } = await api.get('/api/instances/options', { params: scopeParams() });
        opts.value = data;
        if (data.availability_domains && data.availability_domains.length && !createForm.availability_domain) {
          createForm.availability_domain = data.availability_domains[0];
        }
        if (data.images && data.images.length && !createForm.image_id) {
          createForm.image_id = data.images[0].id;
        }
        runPreflight();
      } catch (e) {
        ElMessage?.error(errMsg(e, '加载可用区与镜像失败'));
      } finally {
        optsLoading.value = false;
      }
    }

    async function runPreflight() {
      if (!currentProfile.value) return;
      preflightLoading.value = true;
      try {
        const { data } = await api.post('/api/instances/preflight', {
          profile: currentProfile.value,
          shape: createForm.shape,
          ocpus: createForm.ocpus,
          memory_gb: createForm.memory_gb,
          boot_volume_gb: createForm.boot_gb,
        });
        preflight.value = data;
      } catch (e) {
        preflight.value = null;
      } finally {
        preflightLoading.value = false;
      }
    }

    async function createInstance() {
      if (!createForm.display_name.trim()) return ElMessage?.warning('请输入实例名称');
      creating.value = true;
      createStep.value = '正在向 OCI 提交建机请求…';
      try {
        await api.post('/api/instances/create', {
          profile: currentProfile.value,
          display_name: createForm.display_name,
          shape: createForm.shape,
          ocpus: createForm.ocpus,
          memory_in_gbs: createForm.memory_gb,
          boot_volume_size_in_gbs: createForm.boot_gb,
          availability_domain: createForm.availability_domain,
          fault_domain: createForm.fault_domain || undefined,
          image_id: createForm.image_id,
          assign_ipv6: createForm.assign_ipv6,
          open_all_ports: createForm.open_all_ports,
          capacity_probe: createForm.capacity_probe,
          auto_retry_until_available: createForm.auto_retry_until_available,
          max_retry_minutes: createForm.max_retry_minutes,
          root_password: createForm.use_password ? createForm.root_password : undefined,
          ssh_authorized_keys: createForm.ssh_public_key || undefined,
        });
        ElMessage?.success('建机指令已下发');
        createForm.display_name = randomName();
      } catch (e) {
        ElMessage?.error(errMsg(e, '创建实例失败'));
      } finally {
        creating.value = false;
        createStep.value = '';
      }
    }

    watch(currentProfile, () => {
      opts.value = { availability_domains: [], images: [], subnets: [] };
      createForm.availability_domain = '';
      createForm.image_id = '';
      loadCreateOptions();
    });

    onMounted(() => {
      rollRootPassword();
      loadSshKeys();
      loadCreateOptions();
    });

    return {
      opts,
      optsLoading,
      createForm,
      preflight,
      preflightLoading,
      creating,
      createStep,
      isArm,
      canCreate,
      selectedKeyId,
      sshKeys,
      setShape,
      onOcpuChange,
      runPreflight,
      rollRootPassword,
      onSelectKeyFromVault,
      onPubKeyFile,
      createInstance,
      copy,
      randomName,
    };
  }
};
