// Bundles src/ into public/app.js and public/app.css.   Usage: npm install && npm run build
import { build } from "esbuild";

await build({
  entryPoints: ["src/main.jsx"],
  bundle: true,
  minify: true,
  outfile: "public/app.js",
  jsx: "automatic",
  target: ["es2019"],
  define: { "process.env.NODE_ENV": '"production"' },
  logLevel: "info",
});
