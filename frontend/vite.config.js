import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev server only: forwards /api to the backend's human socket, adding the
// identity header that `tailscale serve` would add in production.
// Requires PLACET_DEV_LOGIN (one of PLACET_ALLOWED_USERS); without it the
// proxy sends no identity and the API answers 401.
const humanSocket =
  process.env.PLACET_HUMAN_SOCKET ||
  `${process.env.XDG_RUNTIME_DIR || '/tmp'}/placet/human.sock`
const devLogin = process.env.PLACET_DEV_LOGIN

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: { socketPath: humanSocket },
        headers: devLogin ? { 'Tailscale-User-Login': devLogin } : {},
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/setupTests.js',
  },
})
