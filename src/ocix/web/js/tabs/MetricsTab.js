import api from '../api.js';
import { currentProfile, scopeParams } from '../store.js';
import { CW, CH, PAD_L, PAD_R, PAD_T, PAD_B } from '../constants.js';
import { errMsg } from '../utils.js';

const { ref, computed, onMounted, watch } = window.Vue;
const { ElMessage } = window.ElementPlus || {};

export default {
  name: 'MetricsTab',
  template: `
    <section class="pane">
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">实例指标</h2>
        </div>
        <div class="toolbar">
          <el-select v-model="metricInstance" placeholder="选择实例" style="width:260px;" @change="loadMetrics">
            <el-option v-for="i in instances" :key="i.id" :value="i.id" :label="i.display_name" />
          </el-select>
          <div class="seg" role="group" aria-label="时间范围">
            <button v-for="h in HOUR_RANGES" :key="h.v" type="button"
                    :class="{ on: metricHours === h.v }" :aria-pressed="metricHours === h.v"
                    @click="metricHours = h.v; loadMetrics()">{{ h.t }}</button>
          </div>
          <el-button @click="loadMetrics" :loading="metricsLoading" :disabled="!metricInstance">查询</el-button>
        </div>

        <template v-if="hasMetricPoints">
          <div class="legend">
            <span v-for="s in chartSeries" :key="s.metric">
              <i :style="{ borderTopStyle: s.dash ? 'dashed' : 'solid', borderTopColor: s.color }"></i>{{ s.name }}
            </span>
          </div>
          <div class="chart" @mousemove="onChartMove" @mouseleave="hoverIdx = -1">
            <svg :viewBox="'0 0 ' + CW + ' ' + CH" role="img"
                 :aria-label="'过去 ' + metricHours + ' 小时的利用率折线图'">
              <line class="grid" v-for="g in gridLines" :key="g.y" x1="46" :y1="g.y" :x2="CW-8" :y2="g.y" />
              <text class="axis-t" v-for="g in gridLines" :key="'t'+g.y" x="40" :y="g.y+3" text-anchor="end">{{ g.label }}</text>
              <path v-for="s in areaSeries" :key="'a'+s.metric" :d="s.area" :fill="s.color" fill-opacity="0.16" />
              <path v-for="s in chartSeries" :key="'l'+s.metric" :d="s.line" fill="none" :stroke="s.color"
                    stroke-width="1.8" :stroke-dasharray="s.dash ? '5 4' : ''" vector-effect="non-scaling-stroke"
                    stroke-linejoin="round" />
              <line v-if="hoverIdx >= 0" class="grid" :x1="hoverX" y1="8" :x2="hoverX" :y2="CH-20"
                    stroke="#38bdf8" stroke-dasharray="3 3" />
              <text class="axis-t" x="46" :y="CH-6">{{ axisStart }}</text>
              <text class="axis-t" :x="CW-8" :y="CH-6" text-anchor="end">{{ axisEnd }}</text>
            </svg>
            <div class="readout" v-if="hoverIdx >= 0">
              <div style="color:var(--text-3);">{{ hoverTime }}</div>
              <div v-for="s in chartSeries" :key="'r'+s.metric" :style="{ color: s.color }">
                {{ s.name }} {{ hoverValue(s) }}
              </div>
            </div>
          </div>
        </template>

        <div class="table-wrap" style="margin-top:16px;" v-if="metrics.length">
          <el-table :data="metrics" size="small" stripe border style="width:100%;">
            <el-table-column label="指标" width="120">
              <template #default="{row}"><b style="color:#ffffff;">{{ row.name }}</b></template>
            </el-table-column>
            <el-table-column label="最新" width="90">
              <template #default="{row}"><span class="mono" style="color:#38bdf8; font-weight:700;">{{ fmtPct(row.latest) }}</span></template>
            </el-table-column>
            <el-table-column label="平均" width="90">
              <template #default="{row}"><span class="mono" style="color:#e2e8f0; font-weight:600;">{{ fmtPct(row.avg) }}</span></template>
            </el-table-column>
            <el-table-column label="峰值" width="90">
              <template #default="{row}"><span class="mono" style="color:#fde047; font-weight:700;">{{ fmtPct(row.max) }}</span></template>
            </el-table-column>
            <el-table-column label="数据点" width="90">
              <template #default="{row}"><span class="mono" style="color:#cbd5e1; font-weight:600;">{{ row.count }}</span></template>
            </el-table-column>
            <el-table-column label="说明" min-width="220">
              <template #default="{row}">
                <span style="color:#fda4af; font-weight:600;" v-if="row.error">{{ row.error }}</span>
                <span class="mono" style="color:#cbd5e1;" v-else-if="row.latest_time">{{ fmtTime(row.latest_time) }}</span>
                <span style="color:#64748b;" v-else>—</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <div class="empty" v-else-if="!metricsLoading">
          <b>请选择实例以查询监控指标</b>
        </div>
      </div>
    </section>
  `,
  setup() {
    const HOUR_RANGES = [
      { v: 1, t: '1h' },
      { v: 6, t: '6h' },
      { v: 24, t: '24h' },
      { v: 72, t: '3d' },
      { v: 168, t: '7d' },
    ];
    const metricHours = ref(24);
    const metricInstance = ref('');
    const instances = ref([]);
    const metrics = ref([]);
    const metricsLoading = ref(false);
    const hoverIdx = ref(-1);

    const fmtPct = (v) => (v === null || v === undefined ? '—' : Number(v).toFixed(1) + '%');
    const fmtTime = (iso) => (iso ? String(iso).slice(5, 16).replace('T', ' ') : '—');

    const hasMetricPoints = computed(() =>
      metrics.value.some((m) => (m.points || []).length > 0)
    );

    const chartSeries = computed(() => {
      const colors = {
        CpuUtilization: '#38bdf8',
        MemoryUtilization: '#fde047',
        DiskBytesRead: '#4ade80',
        DiskBytesWritten: '#fb7185',
      };
      return metrics.value.map((m) => ({
        ...m,
        color: colors[m.metric] || '#cbd5e1',
        dash: m.metric === 'MemoryUtilization',
        line: buildLine(m.points || []),
      }));
    });

    const areaSeries = computed(() =>
      chartSeries.value.map((s) => ({
        ...s,
        area: buildArea(s.points || []),
      }))
    );

    const gridLines = computed(() => [
      { y: PAD_T, label: '100%' },
      { y: PAD_T + (CH - PAD_T - PAD_B) * 0.5, label: '50%' },
      { y: CH - PAD_B, label: '0%' },
    ]);

    const timeExtents = computed(() => {
      const allPts = metrics.value.flatMap((m) => m.points || []);
      if (!allPts.length) return [0, 1];
      const ts = allPts.map((p) => new Date(p.timestamp).getTime());
      return [Math.min(...ts), Math.max(...ts)];
    });

    const axisStart = computed(() => {
      const [min] = timeExtents.value;
      return min ? fmtTime(new Date(min).toISOString()) : '';
    });
    const axisEnd = computed(() => {
      const [, max] = timeExtents.value;
      return max ? fmtTime(new Date(max).toISOString()) : '';
    });

    const hoverX = computed(() => {
      if (hoverIdx.value < 0) return 0;
      const pts = metrics.value[0]?.points || [];
      if (!pts[hoverIdx.value]) return 0;
      const [min, max] = timeExtents.value;
      if (max === min) return PAD_L;
      const t = new Date(pts[hoverIdx.value].timestamp).getTime();
      return PAD_L + ((t - min) / (max - min)) * (CW - PAD_L - PAD_R);
    });

    const hoverTime = computed(() => {
      const pts = metrics.value[0]?.points || [];
      return pts[hoverIdx.value] ? fmtTime(pts[hoverIdx.value].timestamp) : '';
    });

    function hoverValue(s) {
      const pt = (s.points || [])[hoverIdx.value];
      return pt ? Number(pt.value).toFixed(1) + '%' : '—';
    }

    function buildLine(points) {
      if (!points.length) return '';
      const [min, max] = timeExtents.value;
      const span = max === min ? 1 : max - min;
      const plotW = CW - PAD_L - PAD_R;
      const plotH = CH - PAD_T - PAD_B;

      return points
        .map((p, i) => {
          const t = new Date(p.timestamp).getTime();
          const x = PAD_L + ((t - min) / span) * plotW;
          const y = PAD_T + (1 - Math.min(100, Math.max(0, p.value)) / 100) * plotH;
          return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
        })
        .join(' ');
    }

    function buildArea(points) {
      if (!points.length) return '';
      const line = buildLine(points);
      const [min, max] = timeExtents.value;
      const span = max === min ? 1 : max - min;
      const plotW = CW - PAD_L - PAD_R;
      const firstX = PAD_L + ((new Date(points[0].timestamp).getTime() - min) / span) * plotW;
      const lastX = PAD_L + ((new Date(points[points.length - 1].timestamp).getTime() - min) / span) * plotW;
      const bottomY = CH - PAD_B;
      return `${line} L ${lastX.toFixed(1)} ${bottomY} L ${firstX.toFixed(1)} ${bottomY} Z`;
    }

    function onChartMove(e) {
      const rect = e.currentTarget.getBoundingClientRect();
      const clientX = e.clientX - rect.left;
      const svgX = (clientX / rect.width) * CW;
      const pts = metrics.value[0]?.points || [];
      if (!pts.length) return;

      const [min, max] = timeExtents.value;
      const span = max === min ? 1 : max - min;
      const plotW = CW - PAD_L - PAD_R;

      let closest = 0;
      let minDiff = Infinity;
      pts.forEach((p, i) => {
        const t = new Date(p.timestamp).getTime();
        const px = PAD_L + ((t - min) / span) * plotW;
        const diff = Math.abs(px - svgX);
        if (diff < minDiff) {
          minDiff = diff;
          closest = i;
        }
      });
      hoverIdx.value = closest;
    }

    async function loadInstances() {
      if (!currentProfile.value) return;
      try {
        const { data } = await api.get('/api/instances', { params: scopeParams() });
        instances.value = data.instances || [];
        if (instances.value.length && !metricInstance.value) {
          metricInstance.value = instances.value[0].id;
          loadMetrics();
        }
      } catch {}
    }

    async function loadMetrics() {
      if (!metricInstance.value || !currentProfile.value) return;
      metricsLoading.value = true;
      try {
        const { data } = await api.get('/api/instances/metrics', {
          params: {
            profile: currentProfile.value,
            instance_id: metricInstance.value,
            hours: metricHours.value,
          },
        });
        metrics.value = data.metrics || [];
      } catch (e) {
        ElMessage?.error(errMsg(e, '获取监控指标失败'));
      } finally {
        metricsLoading.value = false;
      }
    }

    watch(currentProfile, () => {
      metricInstance.value = '';
      metrics.value = [];
      loadInstances();
    });

    onMounted(loadInstances);

    return {
      HOUR_RANGES,
      metricHours,
      metricInstance,
      instances,
      metrics,
      metricsLoading,
      hasMetricPoints,
      chartSeries,
      areaSeries,
      gridLines,
      hoverIdx,
      hoverX,
      hoverTime,
      axisStart,
      axisEnd,
      CW,
      CH,
      loadMetrics,
      fmtPct,
      fmtTime,
      hoverValue,
      onChartMove,
    };
  }
};
