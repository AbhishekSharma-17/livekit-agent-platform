import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Required by web/Dockerfile (W2-DEPLOY): produces a minimal, self-contained
  // `.next/standalone` server so the runtime image doesn't need `node_modules`
  // or the full pnpm toolchain. See docs/ARCHITECTURE.md §14 / CONTRACTS §12.
  output: "standalone",
};

export default nextConfig;
