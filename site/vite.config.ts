import { defineConfig } from "vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [
    tailwindcss(),
    // Static output: every page is rendered to HTML at build time into dist/client, which is what
    // Vercel serves (see vercel.json; the Vercel project's Root Directory is site/). There is no
    // server code, so no server runtime is needed.
    tanstackStart({ prerender: { enabled: true, crawlLinks: true, failOnError: true } }),
    viteReact(),
  ],
});
