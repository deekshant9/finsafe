import { AlertTriangle } from 'lucide-react'

/** Centered empty/error state used by the data pages. */
export function Notice({ title, body, children }: { title: string; body: string; children: React.ReactNode }) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-4 py-24 text-center">
      <span className="flex size-12 items-center justify-center rounded-2xl bg-primary/8 text-primary"><AlertTriangle className="size-6" /></span>
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="text-muted-foreground">{body}</p>
      {children}
    </div>
  )
}
