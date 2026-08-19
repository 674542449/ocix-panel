/* ── 常用工具函数 ── */

export function copy(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => {
      if (window.ElementPlus?.ElMessage) window.ElementPlus.ElMessage.success('已复制到剪贴板');
    }).catch(() => fallbackCopy(text));
  } else {
    fallbackCopy(text);
  }
}

function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand('copy');
    if (window.ElementPlus?.ElMessage) window.ElementPlus.ElMessage.success('已复制到剪贴板');
  } catch {
    if (window.ElementPlus?.ElMessage) window.ElementPlus.ElMessage.error('复制失败，请手动选择复制');
  }
  document.body.removeChild(ta);
}

export function fmtDate(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } catch {
    return String(iso);
  }
}

export function fmtBytes(bytes) {
  const b = Number(bytes) || 0;
  if (b === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(b) / Math.log(k));
  return (b / Math.pow(k, i)).toFixed(2) + ' ' + sizes[i];
}

export function fmtGb(gb) {
  const v = Number(gb) || 0;
  return v >= 1024 ? (v / 1024).toFixed(2) + ' TB' : v.toFixed(2) + ' GB';
}

export function fmtDay(iso) {
  return iso ? String(iso).slice(0, 10) : '—';
}

export function errMsg(e, fallback = '请求失败') {
  return (
    e?.response?.data?.detail ||
    e?.response?.data?.message ||
    e?.message ||
    fallback
  );
}

/* ── 登录页星球场景 ── */
export const OcixScene = (() => {
  const REGIONS = [
    ['us-ashburn-1',    39.04,  -77.49], ['us-phoenix-1',    33.45, -112.07],
    ['us-sanjose-1',    37.34, -121.89], ['ca-toronto-1',    43.65,  -79.38],
    ['sa-saopaulo-1',  -23.55,  -46.63], ['uk-london-1',     51.51,   -0.13],
    ['eu-frankfurt-1',  50.11,    8.68], ['eu-zurich-1',     47.38,    8.54],
    ['me-dubai-1',      25.20,   55.27], ['ap-mumbai-1',     19.08,   72.88],
    ['ap-singapore-1',   1.35,  103.82], ['ap-tokyo-1',      35.68,  139.69],
    ['ap-osaka-1',      34.69,  135.50], ['ap-sydney-1',    -33.87,  151.21],
  ];

  const toVec = (lat, lon) => {
    const p = lat * Math.PI / 180, l = lon * Math.PI / 180, c = Math.cos(p);
    return [c * Math.sin(l), Math.sin(p), c * Math.cos(l)];
  };
  const rotY = (v, a) => {
    const s = Math.sin(a), c = Math.cos(a);
    return [v[0] * c + v[2] * s, v[1], v[2] * c - v[0] * s];
  };
  const slerp = (a, b, t) => {
    let d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    d = Math.max(-1, Math.min(1, d));
    const th = Math.acos(d) * t;
    const nx = b[0] - a[0] * d, ny = b[1] - a[1] * d, nz = b[2] - a[2] * d;
    const len = Math.hypot(nx, ny, nz);
    if (len < 1e-6) return a.slice();
    const c = Math.cos(th), s = Math.sin(th);
    return [a[0]*c + (nx/len)*s, a[1]*c + (ny/len)*s, a[2]*c + (nz/len)*s];
  };

  const pts = [];
  const N = 450, ga = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(Math.max(0, 1 - y * y)), th = ga * i;
    pts.push([Math.cos(th) * r, y, Math.sin(th) * r]);
  }
  const nodes = REGIONS.map(([name, lat, lon]) => ({ name, v: toVec(lat, lon) }));

  let animId = null, rot = 0;
  let activeArc = null, arcProgress = 0, onFlowCb = null;

  function pickArc() {
    const i = Math.floor(Math.random() * nodes.length);
    let j = Math.floor(Math.random() * (nodes.length - 1));
    if (j >= i) j++;
    activeArc = { from: nodes[i], to: nodes[j] };
    arcProgress = 0;
    if (onFlowCb) onFlowCb(nodes[i].name, nodes[j].name);
  }

  function render(canvas) {
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width = canvas.clientWidth * window.devicePixelRatio;
    const h = canvas.height = canvas.clientHeight * window.devicePixelRatio;
    const R = Math.min(w, h) * 0.42;
    const cx = w * 0.5, cy = h * 0.5;

    ctx.clearRect(0, 0, w, h);
    rot += 0.003;

    // 绘制点阵
    for (let i = 0; i < pts.length; i++) {
      const p = rotY(pts[i], rot);
      if (p[2] < 0) continue;
      const alpha = p[2] * 0.45 + 0.1;
      ctx.fillStyle = `rgba(56, 189, 248, ${alpha})`;
      ctx.beginPath();
      ctx.arc(cx + p[0] * R, cy - p[1] * R, 1.2 * window.devicePixelRatio, 0, Math.PI * 2);
      ctx.fill();
    }

    // 绘制活跃大圆弧
    if (!activeArc) pickArc();
    arcProgress += 0.012;
    if (arcProgress >= 1) pickArc();

    if (activeArc) {
      const v1 = rotY(activeArc.from.v, rot);
      const v2 = rotY(activeArc.to.v, rot);
      const segs = 30;
      ctx.beginPath();
      for (let s = 0; s <= segs; s++) {
        const t = s / segs;
        const pt = rotY(slerp(activeArc.from.v, activeArc.to.v, t), rot);
        const x = cx + pt[0] * R, y = cy - pt[1] * R;
        if (s === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.2)';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // 飞行光点
      const cur = rotY(slerp(activeArc.from.v, activeArc.to.v, arcProgress), rot);
      if (cur[2] >= 0) {
        ctx.fillStyle = '#38bdf8';
        ctx.shadowColor = '#38bdf8';
        ctx.shadowBlur = 10;
        ctx.beginPath();
        ctx.arc(cx + cur[0] * R, cy - cur[1] * R, 3 * window.devicePixelRatio, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
      }
    }

    animId = requestAnimationFrame(() => render(canvas));
  }

  return {
    start(canvas, flowCb) {
      if (animId) cancelAnimationFrame(animId);
      onFlowCb = flowCb;
      if (canvas) render(canvas);
    },
    stop() {
      if (animId) {
        cancelAnimationFrame(animId);
        animId = null;
      }
    }
  };
})();
