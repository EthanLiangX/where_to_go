import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://192.168.3.243:32000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },

      "/titiler": {
        target: "http://192.168.3.243:32001",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/titiler/, ""),
      },

      "/NANSHA": {
        target: "http://192.168.3.243:32000",
        changeOrigin: true,
      },

      "/services": {
        target: "http://192.168.3.243:32001",
        changeOrigin: true,
      },
    },
    watch: {
      usePolling: true
    }
  },
});
