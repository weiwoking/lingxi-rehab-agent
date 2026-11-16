import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendPort = process.env.DOCTOR_BACKEND_PORT ?? "8001";
const backendHttp = `http://127.0.0.1:${backendPort}`;
const backendWs = `ws://127.0.0.1:${backendPort}`;

export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 900 },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": backendHttp,
      "/health": backendHttp,
      "/ws": {
        target: backendWs,
        ws: true
      }
    }
  }
});
