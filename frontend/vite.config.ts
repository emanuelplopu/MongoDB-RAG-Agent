import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Tenant ID for build-time branding (default: recallhub)
// Set via: VITE_TENANT=quellex npm run build
const tenant = process.env.VITE_TENANT || 'recallhub'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:11000',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Include tenant in chunk names for cache busting between tenants
        chunkFileNames: `assets/${tenant}-[name]-[hash].js`,
      },
    },
  },
})
