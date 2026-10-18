/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  devIndicators: false, // hides the dev-only "N" badge
  async redirects() {
    return [{ source: '/dashboard', destination: '/deep-analysis', permanent: true }]
  },
  images: {
    unoptimized: true,
  },
}

export default nextConfig
