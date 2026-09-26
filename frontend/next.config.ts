import type { NextConfig } from "next";

// Static export: the built site is plain files (DigitalOcean static site, or any host).
// The FastAPI backend is the only server; see lib/api.ts for how the browser finds it.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  reactStrictMode: true,
};

export default nextConfig;
