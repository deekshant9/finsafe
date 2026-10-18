import { Analytics } from '@vercel/analytics/next'
import { Toaster } from 'sonner'
import { ChatWidget } from '@/components/chat-widget'
import { Footer, Navbar, ScrollToTop } from '@/components/fin-safe'
import type { Metadata, Viewport } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'FinSafe AI — Your financial risk copilot',
  description: 'Predict cash shortfalls, catch anomalies, and know what to do next with FinSafe AI.',
  generator: 'v0.app',
}

export const viewport: Viewport = {
  colorScheme: 'light dark',
  themeColor: [{ media: '(prefers-color-scheme: light)', color: '#ffffff' }, { media: '(prefers-color-scheme: dark)', color: '#020617' }],
}

// Runs before first paint so a saved dark theme never flashes light. Light stays the default (OS preference ignored).
const THEME_INIT = `try{if(localStorage.getItem('finsafe-theme')==='dark')document.documentElement.classList.add('dark')}catch(e){}`

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en" suppressHydrationWarning><head><script dangerouslySetInnerHTML={{ __html: THEME_INIT }} /></head><body className="antialiased"><Navbar /><ScrollToTop />{children}<Footer /><ChatWidget /><Toaster richColors position="top-right" />{process.env.NODE_ENV === 'production' && <Analytics />}</body></html>
}
