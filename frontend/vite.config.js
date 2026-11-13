export default {
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: process.env.WORKSTATION_BACKEND_URL || 'http://127.0.0.1:8000', ws: true } },
  },
}
