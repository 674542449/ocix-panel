import { errMsg } from './utils.js';

const api = window.axios ? window.axios.create() : null;

// 设置默认请求头
if (api) {
  api.interceptors.request.use((config) => {
    const token = localStorage.getItem('ocix_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });

  api.interceptors.response.use(
    (response) => response,
    (error) => {
      if (error.response && error.response.status === 401) {
        // 未登录或 token 过期
        const isAuthCheck = error.config?.url?.includes('/api/auth/me');
        if (!isAuthCheck) {
          localStorage.removeItem('ocix_token');
          window.location.reload();
        }
      }
      return Promise.reject(error);
    }
  );
}

export default api || window.axios;
