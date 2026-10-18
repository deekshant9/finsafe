'use client'

import { useEffect, useRef, useState } from 'react'
import { Loader2, MessageCircle, Send, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { askChat } from '@/lib/api'
import { cn } from '@/lib/utils'

const SUGGESTIONS = ['What is my biggest risk?', 'Why is my runway short?', 'Which customers should I chase first?']

type Message = { role: 'user' | 'ai'; text: string }

/** Floating chat button (bottom right, on every page) that opens the assistant in a floating window. */
export function ChatWidget() {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Keep the newest message in view by scrolling the chat box only, never the page.
  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight })
  }, [messages, busy, open])

  // Focus the question box when the window opens; Escape closes it (it is not modal, so the page stays usable).
  useEffect(() => {
    if (!open) return
    inputRef.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const ask = async (question: string) => {
    const q = question.trim()
    if (!q || busy) return
    setInput('')
    setMessages((m) => [...m, { role: 'user', text: q }])
    setBusy(true)
    try {
      const res = await askChat(q)
      setMessages((m) => [...m, { role: 'ai', text: res.answer }])
    } catch (e) {
      setMessages((m) => [...m, { role: 'ai', text: e instanceof Error ? e.message : "Sorry, I couldn't answer that." }])
    }
    setBusy(false)
  }

  return (
    <>
      {open && (
        <section
          role="dialog"
          aria-label="FinSafe chat"
          className="fixed bottom-24 right-5 z-50 flex h-[min(34rem,calc(100dvh-8rem))] w-[min(24rem,calc(100vw-2.5rem))] flex-col overflow-hidden rounded-2xl border bg-popover text-popover-foreground shadow-2xl shadow-primary/15 animate-in fade-in slide-in-from-bottom-2 duration-200"
        >
          <header className="flex items-start justify-between gap-3 border-b bg-muted/30 p-4">
            <div>
              <h2 className="text-sm font-semibold">Ask FinSafe</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">Answers come only from your connected data and analysis.</p>
            </div>
            <div className="flex items-center gap-1">
              {messages.length > 0 && !busy && <Button variant="ghost" size="xs" onClick={() => setMessages([])}>Clear</Button>}
              <Button variant="ghost" size="icon-sm" aria-label="Close chat" onClick={() => setOpen(false)}><X /></Button>
            </div>
          </header>

          <div ref={box} className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4" aria-live="polite">
            {messages.length === 0 && (
              <div className="flex flex-wrap gap-2">
                {SUGGESTIONS.map((s) => <Button key={s} variant="outline" size="sm" onClick={() => ask(s)}>{s}</Button>)}
              </div>
            )}
            {messages.map((m, i) => (
              <p key={i} className={cn('max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-6', m.role === 'user' ? 'self-end bg-primary text-primary-foreground' : 'self-start bg-muted')}>{m.text}</p>
            ))}
            {busy && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Thinking…</p>}
          </div>

          <form className="flex gap-2 border-t p-3" onSubmit={(e) => { e.preventDefault(); ask(input) }}>
            <Input ref={inputRef} aria-label="Ask a question about your finances" placeholder="Ask about your cash, customers or vendors" value={input} onChange={(e) => setInput(e.target.value)} />
            <Button type="submit" size="icon" aria-label="Send question" disabled={busy || !input.trim()}><Send /></Button>
          </form>
        </section>
      )}

      <Button
        size="icon"
        className="fixed bottom-5 right-5 z-50 size-14 rounded-full shadow-xl shadow-primary/25"
        aria-label={open ? 'Close FinSafe chat' : 'Open FinSafe chat'}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {open ? <X className="size-6" /> : <MessageCircle className="size-6" />}
      </Button>
    </>
  )
}
