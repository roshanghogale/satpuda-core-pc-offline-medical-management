import './vite-unc-patch.mjs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const diskRoot = path.dirname(fileURLToPath(import.meta.url)).replace(/\\/g, '/')

export default defineConfig({
  root: diskRoot,
  cacheDir: path.join(process.env.LOCALAPPDATA || diskRoot, 'satpuda-vite-cache'),
  resolve: {
    preserveSymlinks: true,
  },
  plugins: [react()],
  clearScreen: false,
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // Avoid first-paint 404 while Vite is still resolving the mapped F: drive.
    preTransformRequests: false,
    fs: {
      strict: false,
      allow: [diskRoot],
    },
    watch: {
      usePolling: true,
      interval: 400,
      ignored: ['**/src-tauri/**', '**/node_modules/**'],
    },
  },
  envPrefix: ['VITE_', 'TAURI_'],
  build: {
    target: 'esnext',
    minify: !process.env.TAURI_DEBUG ? 'esbuild' : false,
    sourcemap: !!process.env.TAURI_DEBUG,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/react-dom') || id.includes('node_modules/react/')) {
            return 'vendor-react'
          }
          if (id.includes('/pages/SettingsPage') || id.includes('/pages/settings/')) {
            return 'page-settings'
          }
          if (id.includes('/pages/SalesPage') || id.includes('/pages/PurchasePage')) {
            return 'page-billing'
          }
        },
      },
    },
  },
})
