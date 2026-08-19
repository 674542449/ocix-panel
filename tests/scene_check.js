/* 把登录页的场景模块抠出来，在 Node 里用假画布跑，验证数学没错。
   浏览器窗格是 hidden 文档，rAF 一次都不触发，动画在那儿观察不到。 */
const fs = require('fs');
const path = require('path');

const UTILS = path.join(__dirname, '..', 'src', 'ocix', 'web', 'js', 'utils.js');
const src = fs.readFileSync(UTILS, 'utf8');
const target = 'export const OcixScene = (() => {';
const start = src.indexOf(target);
if (start < 0) { console.error('找不到 OcixScene'); process.exit(1); }

// 数大括号取整块
let depth = 0, i = src.indexOf('{', start), end = -1;
for (let k = i; k < src.length; k++) {
  if (src[k] === '{') depth++;
  else if (src[k] === '}') { depth--; if (depth === 0) { end = k; break; } }
}
const body = src.slice(start, src.indexOf(';', end) + 1).replace('export const OcixScene =', 'const OcixScene =');

// ---- 假环境 ----
const calls = { arc: 0, moveTo: 0, lineTo: 0, stroke: 0, fill: 0, bad: [] };
const num = (label, ...vs) => vs.forEach(v => {
  if (typeof v === 'number' && !Number.isFinite(v)) calls.bad.push(label + ':' + v);
});
const ctx2d = {
  clearRect(){}, beginPath(){}, closePath(){},
  arc(x, y, r){ num('arc', x, y, r); calls.arc++; },
  moveTo(x, y){ num('moveTo', x, y); calls.moveTo++; },
  lineTo(x, y){ num('lineTo', x, y); calls.lineTo++; },
  stroke(){ calls.stroke++; }, fill(){ calls.fill++; },
  setTransform(){}, createRadialGradient(){ return { addColorStop(){} }; },
  set fillStyle(v){ if (/NaN|undefined/.test(String(v))) calls.bad.push('fillStyle:' + v); },
  set strokeStyle(v){ if (/NaN|undefined/.test(String(v))) calls.bad.push('strokeStyle:' + v); },
  set lineWidth(v){ num('lineWidth', v); },
};
const canvas = { clientWidth: 1440, clientHeight: 900, width: 0, height: 0,
                 getContext: () => ctx2d };

let rafCbs = [];
global.window = {
  devicePixelRatio: 2, innerWidth: 1600, innerHeight: 900,
  matchMedia: () => ({ matches: false }),
  addEventListener(){}, removeEventListener(){},
};
global.document = { hidden: false };
const roCallbacks = [];
global.ResizeObserver = class { constructor(cb){ roCallbacks.push(cb); } observe(){} disconnect(){} };
global.window.ResizeObserver = global.ResizeObserver;
global.performance = { now: () => Date.now() };
global.requestAnimationFrame = (cb) => { rafCbs.push(cb); return rafCbs.length; };
global.cancelAnimationFrame = () => { rafCbs = []; };

const OcixScene = eval(body + '; OcixScene');

// ---- 跑起来 ----
const flows = [];
OcixScene.start(canvas, (a, b) => flows.push(a + ' -> ' + b));

let t = performance.now();
for (let frame = 0; frame < 900; frame++) {           // 约 15 秒（60fps）
  t += 16.7;
  const cbs = rafCbs; rafCbs = [];
  cbs.forEach(cb => cb(t));
}

const ok = [], bad = [];
const check = (name, cond, detail) => (cond ? ok : bad).push(name + (detail ? ' — ' + detail : ''));

check('画布按 DPR 放大', canvas.width === 1440 * 2 && canvas.height === 900 * 2,
      canvas.width + 'x' + canvas.height);
check('每帧都在画东西', calls.arc > 1000, 'arc 调用 ' + calls.arc + ' 次');
check('弧线有描边', calls.stroke > 100, 'stroke ' + calls.stroke + ' 次');
check('坐标全是有限数（NaN 会让画布静默空白）', calls.bad.length === 0,
      calls.bad.slice(0, 5).join(', ') || '无异常值');
check('数据包跑完会回调', flows.length > 0, flows.length + ' 趟：' + flows.slice(0, 3).join(' | '));

// 回调里的名字必须是真实区域名，且首尾不同
const REGION_RE = /^(us|eu|uk|ap|sa|me|ca)-[a-z]+-\d$/;
const names = [...new Set(flows.flatMap(f => f.split(' -> ')))];
check('回调给的是真实区域名', names.length > 0 && names.every(n => REGION_RE.test(n)),
      names.slice(0, 4).join(', '));
check('链路两端不同', flows.every(f => { const [a, b] = f.split(' -> '); return a !== b; }));

OcixScene.stop();
check('stop() 之后 rAF 队列清空', rafCbs.length === 0);

console.log('\n通过：');
ok.forEach(x => console.log('  [OK] ' + x));
if (bad.length) { console.log('\n失败：'); bad.forEach(x => console.log('  [X] ' + x)); }
console.log('\n' + ok.length + ' 项通过，' + bad.length + ' 项失败');
process.exit(bad.length ? 1 : 0);
