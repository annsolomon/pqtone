import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development, run the stack (make up) and proxy API + auth through the gateway.
export default defineConfig({
  plugins: [react()],
  build: { sourcemap: false, target: "es2022" },
  server: {
    port: 5173,
    proxy: {
      "^/(api|oauth2|login|logout|auth)": { target: "http://localhost:8080", changeOrigin: false },
    },
  },
  test: { environment: "node" },
});
