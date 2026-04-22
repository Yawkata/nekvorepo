import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits a self-contained server bundle at .next/standalone with a minimal
  // node_modules tree. Required for the small runtime image in Dockerfile.
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  compress: true,
};

export default nextConfig;
