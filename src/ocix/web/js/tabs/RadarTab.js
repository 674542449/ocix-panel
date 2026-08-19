import api from '../api.js';
import { currentProfile, activeTab } from '../store.js';
import { errMsg } from '../utils.js';

const { ref, reactive, onMounted, watch } = window.Vue;
const { ElMessage } = window.ElementPlus || {};

export default {
  name: 'RadarTab',
  template: `
    <section class="pane">
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">全区域容量雷达 (纯探测 · 不建机)</h2>
          <div style="display:flex; align-items:center; gap:8px;">
            <el-button type="primary" :loading="radarLoading" @click="scanRadar">
              <svg class="btn-icon" :class="{ spin: radarLoading }" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm0 18a8 8 0 1 1 8-8 8 8 0 0 1-8 8zm0-14a6 6 0 1 0 6 6 6 6 0 0 0-6-6zm0 10a4 4 0 1 1 4-4 4 4 0 0 1-4 4z"/></svg>
              {{ radarLoading ? '雷达扫描中…' : '立即扫描容量雷达' }}
            </el-button>
          </div>
        </div>

        <div class="radar-banner">
          <div>
            <div style="font-size:14px; font-weight:700; color:#ffffff; margin-bottom:4px;">
              Oracle 官方 ComputeCapacityReport 探测引擎
            </div>
            <div style="font-size:12px; color:var(--text-2);">
              实时向 OCI 获取各可用区 (AD) 与故障域 (Fault Domain) 的放货与库存状态，纯只读探测，绝不创建任何实例，零风控消耗。
            </div>
          </div>
          <div class="radar-stats" v-if="radarData">
            <div class="radar-stat-item">
              <span class="radar-stat-k">扫描区域</span>
              <span class="radar-stat-v">{{ radarData.scanned_regions_count }} 个</span>
            </div>
            <div class="radar-stat-item">
              <span class="radar-stat-k">有货位置</span>
              <span class="radar-stat-v" :class="{ ok: radarData.total_available_locations > 0 }">
                {{ radarData.total_available_locations }} 处
              </span>
            </div>
            <div class="radar-stat-item">
              <span class="radar-stat-k">探测耗时</span>
              <span class="radar-stat-v mono">{{ radarData.elapsed_seconds }}s</span>
            </div>
          </div>
        </div>

        <!-- 扫描选项 -->
        <div style="margin-bottom:18px; padding:12px 14px; background:rgba(30,41,59,0.35); border-radius:10px; border:1px solid rgba(255,255,255,0.06);">
          <div style="display:flex; align-items:center; gap:16px; flex-wrap:wrap;">
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-size:12.5px; color:var(--text-3); font-weight:600;">探测规格:</span>
              <div class="seg" role="group" aria-label="雷达规格">
                <button type="button" :class="{ on: radarForm.shape === 'VM.Standard.A1.Flex' && radarForm.ocpus === 4 }"
                        @click="setRadarSpec('VM.Standard.A1.Flex', 4, 24)">A1 4C/24G</button>
                <button type="button" :class="{ on: radarForm.shape === 'VM.Standard.A1.Flex' && radarForm.ocpus === 2 }"
                        @click="setRadarSpec('VM.Standard.A1.Flex', 2, 12)">A1 2C/12G</button>
                <button type="button" :class="{ on: radarForm.shape === 'VM.Standard.A1.Flex' && radarForm.ocpus === 1 }"
                        @click="setRadarSpec('VM.Standard.A1.Flex', 1, 6)">A1 1C/6G</button>
                <button type="button" :class="{ on: radarForm.shape === 'VM.Standard.E2.1.Micro' }"
                        @click="setRadarSpec('VM.Standard.E2.1.Micro', 1, 1)">AMD Micro</button>
              </div>
            </div>

            <div style="display:flex; align-items:center; gap:8px; margin-left:auto;">
              <el-switch v-model="radarForm.all_regions" active-text="扫描全部已订阅区域" />
            </div>
          </div>

          <div v-if="radarForm.shape === 'VM.Standard.E2.1.Micro'" style="margin-top:10px; padding:6px 10px; background:rgba(56,189,248,0.08); border-radius:6px; font-size:11.5px; color:var(--text-2); border-left:3px solid var(--accent);">
            注：E2.1.Micro 为 OCI 早期固定共享规格，Oracle 官方 ComputeCapacityReport 探测引擎主要针对 A1.Flex 等弹性规格。对于 Micro 规格，OCI 接口常返回无库存假象，实际通常可直接在「新建实例」成功开机。
          </div>
        </div>

        <!-- 扫描中骨架屏 -->
        <div v-if="radarLoading" style="padding:16px 0;">
          <div style="display:flex; align-items:center; gap:10px; margin-bottom:14px; color:#38bdf8; font-weight:600; font-size:13px;">
            <i class="dot ok"></i><span>正在并发连接 OCI 区域端点，获取各可用区容量报告…</span>
          </div>
          <div class="skel skel-row" v-for="i in 4" :key="i"></div>
        </div>

        <!-- 扫描结果列表 -->
        <div v-else-if="radarData">
          <div v-if="!radarData.regions || !radarData.regions.length" class="empty">未找到任何可用区域</div>
          <div v-for="it in radarData.regions" :key="it.region"
               class="radar-region-card" :class="{ 'has-stock': it.has_capacity }">
            <div class="radar-region-head">
              <div class="radar-region-title">
                <span>{{ it.region }}</span>
                <span class="who" v-if="it.is_current">（当前默认区域）</span>
                <span v-if="it.has_capacity" class="radar-badge ok">有可用容量 AVAILABLE</span>
                <span v-else-if="!it.error" class="radar-badge crit">暂无库存 NO CAPACITY</span>
                <span v-else class="radar-badge crit">查询失败</span>
              </div>
              <div v-if="it.has_capacity" style="font-size:12px; color:#4ade80; font-weight:600;">
                发现可用放货！
              </div>
            </div>

            <div v-if="it.error" style="color:var(--crit); font-size:12px; padding:6px 0;">
              探测错误：{{ it.error }}
            </div>

            <div v-else class="radar-ad-grid">
              <div v-for="item in it.ads" :key="item.availability_domain"
                   class="radar-ad-box" :class="{ ok: item.has_capacity }">
                <div class="radar-ad-name">
                  <span>{{ item.availability_domain }}</span>
                  <span :class="['radar-badge', item.has_capacity ? 'ok' : 'crit']">
                    {{ item.has_capacity ? '有货' : '无货' }}
                  </span>
                </div>

                <div class="radar-fd-list">
                  <div v-for="fd in item.fault_domains" :key="fd.fault_domain"
                       class="radar-fd-row" :class="{ ok: fd.available }">
                    <span class="mono">{{ fd.fault_domain }}</span>
                    <span style="font-weight:600;">{{ fd.available ? 'AVAILABLE (有货)' : '缺货' }}</span>
                  </div>
                </div>

                <div v-if="item.has_capacity && it.is_current" style="margin-top:10px;">
                  <el-button type="primary" size="small" style="width:100%;"
                             @click="jumpToCreateWithAd(item.availability_domain)">
                    使用此可用域去开机
                  </el-button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-else class="empty" style="padding:40px 10px;">
          <b>点击「立即扫描容量雷达」开始检测</b>
          <p style="color:var(--text-3); font-size:12px; margin-top:6px;">
            支持一键探测当前账户所在区域或全球已订阅区域的 A1.Flex 及 Micro 规格库存
          </p>
        </div>
      </div>
    </section>
  `,
  setup() {
    const radarLoading = ref(false);
    const radarData = ref(null);
    const radarForm = reactive({
      shape: 'VM.Standard.A1.Flex',
      ocpus: 4,
      memory_gb: 24,
      all_regions: false,
    });

    function setRadarSpec(shape, ocpus, mem) {
      radarForm.shape = shape;
      radarForm.ocpus = ocpus;
      radarForm.memory_gb = mem;
    }

    async function scanRadar() {
      if (!currentProfile.value) return;
      radarLoading.value = true;
      try {
        const { data } = await api.post('/api/instances/capacity-radar', {
          profile: currentProfile.value,
          shape: radarForm.shape,
          ocpus: radarForm.ocpus,
          memory_in_gbs: radarForm.memory_gb,
          all_regions: radarForm.all_regions,
        });
        radarData.value = data;
        if (data.total_available_locations > 0) {
          ElMessage?.success(`雷达扫描完成！发现 ${data.total_available_locations} 处有货`);
        } else {
          ElMessage?.info('雷达扫描完成，所选区域暂无可用库存');
        }
      } catch (e) {
        ElMessage?.error(errMsg(e, '容量雷达扫描失败'));
      } finally {
        radarLoading.value = false;
      }
    }

    function jumpToCreateWithAd(ad) {
      activeTab.value = 'create';
    }

    watch(currentProfile, () => {
      radarData.value = null;
    });

    return {
      radarLoading,
      radarData,
      radarForm,
      setRadarSpec,
      scanRadar,
      jumpToCreateWithAd,
    };
  }
};
