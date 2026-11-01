import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// GitHub Pages serves the project site under /<repo>/
export default defineConfig({
  base: process.env.SITE_BASE ?? "/agentplayground/",
  plugins: [react()],
});
