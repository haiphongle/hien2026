import path from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

function remoteAccessGuard(): Plugin {
  const username = process.env.FLOWKIT_WEB_USER
  const password = process.env.FLOWKIT_WEB_PASSWORD

  return {
    name: 'flowkit-remote-access-guard',
    configureServer(server) {
      if (!username || !password) return
      server.middlewares.use((req, res, next) => {
        const header = typeof req.headers?.authorization === 'string' ? req.headers.authorization : ''
        const encoded = header.startsWith('Basic ') ? header.slice(6) : ''
        let valid = false
        try {
          const decoded = Buffer.from(encoded, 'base64').toString('utf8')
          const separator = decoded.indexOf(':')
          valid = separator >= 0
            && decoded.slice(0, separator) === username
            && decoded.slice(separator + 1) === password
        } catch {
          valid = false
        }
        if (valid) {
          next()
          return
        }
        res.statusCode = 401
        res.setHeader('WWW-Authenticate', 'Basic realm="FlowKit"')
        res.end('Authentication required')
      })
    },
  }
}

export default defineConfig({
  plugins: [remoteAccessGuard(), react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    allowedHosts: process.env.FLOWKIT_ALLOWED_HOST ? [process.env.FLOWKIT_ALLOWED_HOST] : [],
    proxy: {
      '/api': 'http://127.0.0.1:8100',
      '/ws': { target: 'ws://127.0.0.1:8100', ws: true },
      '/health': 'http://127.0.0.1:8100',
    }
  },
  build: { outDir: 'dist' }
})
