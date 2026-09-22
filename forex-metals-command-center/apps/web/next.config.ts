import path from "node:path";
import type { NextConfig } from "next";

const repoRoot = path.resolve(process.cwd(), "../..");

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  transpilePackages: ["@fmcc/shared-types"],
  outputFileTracingRoot: repoRoot,
  turbopack: { root: repoRoot },
  // Hosts (besides localhost) allowed to load Next.js dev resources / HMR. Needed when reaching the
  // dashboard by this machine's name or over Tailscale. Add your exact tailnet name/IP if it differs
  // (from `tailscale status`), e.g. "londa-desktop.<tailnet>.ts.net" or "100.x.y.z". Dev-only setting.
  allowedDevOrigins: ["londa-desktop", "*.ts.net"],
};

export default nextConfig;
