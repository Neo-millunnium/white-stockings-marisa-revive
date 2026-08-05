import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Vite 配置:
// - dev server 跑在 8888 端口
// - 开发环境把 /api 开头的请求去掉前缀后代理到后端(127.0.0.1:3000),避免跨域
// - 生产环境构建产物使用相对路径 /api,由反向代理(nginx 等)或后端路由组转发
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 8888,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:3000',
        changeOrigin: true,
        // 去掉 /api 前缀,后端接口直接挂在根路径(/Add /Reply /Forget /Status)
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
