import type { NextConfig } from "next";

const apiProxyTarget =
  process.env.BASERENDER_API_PROXY_TARGET ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/media/:path*",
        destination: `${apiProxyTarget}/media/:path*`,
      },
      {
        source: "/jobs/:path*",
        destination: `${apiProxyTarget}/jobs/:path*`,
      },
      {
        source: "/transcode",
        destination: `${apiProxyTarget}/transcode`,
      },
    ];
  },
};

export default nextConfig;
