import api from '../api.js';
import { currentProfile, tier, tiers, scopeParams } from '../store.js';
import { TIER_DOT } from '../constants.js';
import { fmtGb, errMsg } from '../utils.js';

const { ref, onMounted, watch } = window.Vue;
const { ElMessage } = window.ElementPlus || {};

export default {
  name: 'UsageTab',
  template: `
    <section class="pane">
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">账户等级</h2>
          <el-button size="small" @click="loadTier(true)" :loading="tierLoading">
            {{ tier.tier ? '重新检测' : '检测' }}
          </el-button>
        </div>

        <div class="kv" v-if="tier.tier">
          <span class="state">
            <i :class="['dot', tierDotOf(tier)]"></i>
            <span class="state-label" style="font-size:13px;">{{ tier.label }}</span>
          </span>
          <span class="who">{{ (tier.reasons || [])[0] }}</span>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <h2 class="card-title">当月出网流量</h2>
          <el-button size="small" @click="loadEgress(true)" :loading="egressLoading">
            {{ egress.egress_gb === undefined ? '查询' : '重新查询' }}
          </el-button>
        </div>
        <template v-if="egress.egress_gb !== undefined">
          <div class="quota">
            <div class="label">出网流量（{{ egress.region || '当前区域' }}）</div>
            <div :class="['bar', egress.percent >= 100 ? 'over' : (egress.percent >= 80 ? 'warn' : '')]">
              <i :style="{ width: Math.min(egress.percent, 100) + '%' }"></i>
            </div>
            <div class="num"><b>{{ fmtGb(egress.egress_gb) }}</b> / 10 TB</div>
          </div>
          <p class="hint" v-if="egress.error" style="color:var(--crit); margin-top:8px;">{{ egress.error }}</p>
        </template>
      </div>

      <div class="card" v-if="usageLoading && !usage" aria-label="加载中">
        <div class="skel skel-row" v-for="i in 6" :key="i"></div>
      </div>
      <div class="card" v-else-if="usage">
        <div class="card-head">
          <h2 class="card-title">当前用量 / Always Free 上限</h2>
        </div>
        <el-alert v-for="(w,i) in (usage.warnings || [])" :key="i" type="error" show-icon :closable="false"
                  :title="w" style="margin-bottom:8px;" />
        <div class="quota" v-for="it in (usage.items || [])" :key="it.label">
          <div class="label">{{ it.label }}</div>
          <div :class="['bar', it.over ? 'over' : (it.percent >= 80 ? 'warn' : '')]">
            <i :style="{ width: Math.min(it.percent, 100) + '%' }"></i>
          </div>
          <div class="num"><b>{{ it.used }}</b> / {{ it.limit }} {{ it.unit }}</div>
        </div>
      </div>
      <div class="card empty" v-else-if="!usageLoading">
        <b>暂无额度数据</b>
      </div>
    </section>
  `,
  setup() {
    const tierLoading = ref(false);
    const egress = ref({});
    const egressLoading = ref(false);
    const usage = ref(null);
    const usageLoading = ref(false);

    const tierDotOf = (t) => TIER_DOT[t && t.tier] || 'idle';

    async function loadTier(force = false) {
      if (!currentProfile.value) return;
      tierLoading.value = true;
      try {
        const { data } = await api.get(
          `/api/profiles/${encodeURIComponent(currentProfile.value)}/tier`,
          { params: { limits: true, refresh: !!force } }
        );
        tier.value = data;
        tiers.value = { ...tiers.value, [currentProfile.value]: data };
      } catch (e) {
        ElMessage?.error(errMsg(e, '账户等级检测失败'));
      } finally {
        tierLoading.value = false;
      }
    }

    async function loadEgress(force = false) {
      if (!currentProfile.value) return;
      egressLoading.value = true;
      try {
        const { data } = await api.get('/api/provision/egress', {
          params: { profile: currentProfile.value, refresh: !!force },
        });
        egress.value = data;
      } catch (e) {
        egress.value = { error: errMsg(e, '查询流量失败') };
      } finally {
        egressLoading.value = false;
      }
    }

    async function loadUsage() {
      if (!currentProfile.value) return;
      usageLoading.value = true;
      try {
        const { data } = await api.get('/api/instances/usage', { params: scopeParams() });
        usage.value = data;
      } catch (e) {
        // 忽略
      } finally {
        usageLoading.value = false;
      }
    }

    watch(currentProfile, () => {
      loadUsage();
      loadEgress(false);
    });

    onMounted(() => {
      loadUsage();
      loadEgress(false);
    });

    return {
      tier,
      tierLoading,
      egress,
      egressLoading,
      usage,
      usageLoading,
      tierDotOf,
      loadTier,
      loadEgress,
      fmtGb,
    };
  }
};
