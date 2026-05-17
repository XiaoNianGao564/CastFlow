import { defineConfig } from "vite"
import solidPlugin from "vite-plugin-solid"
import tailwindcss from "@tailwindcss/vite"

const apiPort = process.env.API_PORT || 3001

export default defineConfig({
  plugins: [solidPlugin(), tailwindcss()],
  server: {
    port: 3000,
    host: "0.0.0.0",
    proxy: {
      "/api": `http://localhost:${apiPort}`,
    },
  },
  build: {
    target: "esnext",
  },
})
