export const CW = 720, CH = 200, PAD_L = 46, PAD_R = 8, PAD_T = 8, PAD_B = 26;

export const NAV = [
  { title: '总览', items: [
    { name: 'instances', label: '实例', icon: 'M2 2h20v8H2zM2 14h20v8H2zM6 6h.01M6 18h.01' },
  ]},
  { title: '资源', items: [
    { name: 'create', label: '新建实例', icon: 'M12 5v14M5 12h14' },
    { name: 'storage', label: '存储', icon: 'M3 5a9 3 0 1 0 18 0A9 3 0 1 0 3 5zM3 5v14a9 3 0 0 0 18 0V5M3 12a9 3 0 0 0 18 0' },
    { name: 'firewall', label: '防火墙', icon: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z' },
    { name: 'ssh_keys', label: 'SSH 公钥', icon: 'm21 2-2 2m-1.5 1.5L14 9l-1.5-1.5L11 9l-1.5-1.5L8 9l-1.5-1.5L5 9l-3 3 7 7 3-3' },
  ]},
  { title: '观测', items: [
    { name: 'radar', label: '容量雷达', icon: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm0 18a8 8 0 1 1 8-8 8 8 0 0 1-8 8zm0-14a6 6 0 1 0 6 6 6 6 0 0 0-6-6zm0 10a4 4 0 1 1 4-4 4 4 0 0 1-4 4z' },
    { name: 'usage', label: '免费额度', icon: 'M21.21 15.89A10 10 0 1 1 8 2.83M22 12A10 10 0 0 0 12 2v10z' },
    { name: 'metrics', label: '监控', icon: 'M3 3v18h18M19 9l-5 5-4-4-3 3' },
    { name: 'billing', label: '财务中心', icon: 'M2 5h20v14H2zM2 10h20' },
    { name: 'audit', label: '审计', icon: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8' },
  ]},
  { title: '设置', items: [
    { name: 'profile', label: '账户配置', icon: 'M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8z' },
    { name: 'notification', label: 'TG 通知', icon: 'm22 2-7 20-4-9-9-4zM22 2 11 13' },
    { name: 'password', label: '密码', icon: 'M3 11h18v11H3zM7 11V7a5 5 0 0 1 10 0v4' },
    { name: 'update', label: '更新', icon: 'M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8M3 3v5h5M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16M16 21h5v-5' },
  ]},
];

export const TIER_DOT = { paid: 'attn', free: 'ok', unknown: 'idle' };
export const TIER_SHORT = { paid: '已升级', free: '免费', unknown: '未知' };
export const ACTION_LABEL = { START: '开机', SOFTSTOP: '关机', STOP: '强制关机', SOFTRESET: '重启', RESET: '强制重启' };

export const NAME_DOMAIN = ['payments', 'orders', 'search', 'media', 'notify', 'billing', 'analytics',
  'identity', 'inventory', 'chat', 'ledger', 'catalog', 'session', 'webhook', 'report'];
export const NAME_ROLE = ['api', 'worker', 'gateway', 'cache', 'proxy', 'edge', 'relay', 'queue',
  'sync', 'ingest', 'render', 'cron'];
export const NAME_ENV = ['prod', 'stg', 'live', 'core', 'main'];

export function randomName() {
  const pick = a => a[Math.floor(Math.random() * a.length)];
  const nn = String(Math.floor(Math.random() * 8) + 1).padStart(2, '0');
  const d = pick(NAME_DOMAIN), r = pick(NAME_ROLE);
  switch (Math.floor(Math.random() * 3)) {
    case 0: return `${d}-${r}-${nn}`;
    case 1: return `${d}-${r}-${pick(NAME_ENV)}`;
    default: return `${r}-${d}-${nn}`;
  }
}
