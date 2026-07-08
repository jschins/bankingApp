import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// During development, proxy /api to the FastAPI back-end so the browser can use
// same-origin requests (no CORS needed in dev).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://192.168.7.75:8100",
        changeOrigin: true,
      },
    },
  },
});
