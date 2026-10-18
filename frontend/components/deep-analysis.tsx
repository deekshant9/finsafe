'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { AlertTriangle, ArrowRight, Check, Copy, Lightbulb, Loader2, Mail, RefreshCw, ShieldCheck, Sparkles } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { HealthGauge, RunwayChart } from '@/components/live-preview'
import { ApiError, draftEmail, getAnalysis, type Analysis, type EmailDraft, type Finding } from '@/lib/api'
import { formatMoney, plural } from '@/lib/format'
import { cn } from '@/lib/utils'

const errorMessage = (e: unknown) => (e instanceof Error ? e.message : 'Something went wrong')

const severityStyle: Record<Finding['severity'], string> = {
  high: 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300',
  medium: 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300',
  low: 'border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-500/30 dark:bg-slate-500/10 dark:text-slate-300',
}

type State = { kind: 'loading' } | { kind: 'empty' } | { kind: 'error'; message: string } | { kind: 'ready'; analysis: Analysis }

export function DeepAnalysisPage() {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [explaining, setExplaining] = useState(false)
  const [reloading, setReloading] = useState(false)

  const load = async () => {
    try {
      // Numbers first (instant), then the prose explanations (may call Gemini).
      const fast = await getAnalysis(false)
      setState({ kind: 'ready', analysis: fast })
      if (fast.findings.length) {
        setExplaining(true)
        const explained = await getAnalysis(true)
        setState({ kind: 'ready', analysis: explained })
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setState({ kind: 'empty' })
      else setState((prev) => (prev.kind === 'ready' ? prev : { kind: 'error', message: errorMessage(e) }))
    } finally {
      setExplaining(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  return (
    <>
      <main className="mx-auto max-w-6xl px-5 py-10 lg:px-8 lg:py-14">
        {state.kind === 'loading' && (
          <div className="flex items-center justify-center gap-2 py-32 text-muted-foreground"><Loader2 className="size-5 animate-spin" />Analyzing your finances…</div>
        )}
        {state.kind === 'empty' && (
          <EmptyState title="No data to analyze yet" body="Connect Zoho Books, NetSuite or Odoo and we'll build your risk view from your own records." />
        )}
        {state.kind === 'error' && (
          <EmptyState title="Couldn't load your analysis" body={state.message} action={<Button variant="outline" onClick={() => { setState({ kind: 'loading' }); load() }}>Retry</Button>} />
        )}
        {state.kind === 'ready' && (
          <Analysis
            a={state.analysis}
            explaining={explaining}
            reloading={reloading}
            onRefresh={async () => {
              setReloading(true)
              await load()
              setReloading(false)
            }}
          />
        )}
      </main>
    </>
  )
}

function EmptyState({ title, body, action }: { title: string; body: string; action?: React.ReactNode }) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-4 py-24 text-center">
      <span className="flex size-12 items-center justify-center rounded-2xl bg-primary/8 text-primary"><AlertTriangle className="size-6" /></span>
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="text-muted-foreground">{body}</p>
      {action ?? <Link href="/integrations" className={buttonVariants({ size: 'lg' })}>Connect your data <ArrowRight data-icon="inline-end" /></Link>}
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'bad' }) {
  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <p className={cn('mt-1 text-xl font-semibold tracking-tight', tone === 'bad' && 'text-destructive')}>{value}</p>
      </CardContent>
    </Card>
  )
}

function Analysis({ a, explaining, reloading, onRefresh }: { a: Analysis; explaining: boolean; reloading: boolean; onRefresh: () => void }) {
  const money = (n: number) => formatMoney(n, a.currency)
  const [draft, setDraft] = useState<EmailDraft | null>(null)
  const [drafting, setDrafting] = useState<string | null>(null)

  const openDraft = async (invoiceId: string) => {
    setDrafting(invoiceId)
    try {
      setDraft(await draftEmail(invoiceId))
    } catch (e) {
      toast.error("Couldn't draft the email", { description: errorMessage(e) })
    }
    setDrafting(null)
  }

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <Badge variant="outline" className="mb-3 border-primary/20 bg-primary/5 text-primary">Deep analysis</Badge>
          <h1 className="text-3xl font-semibold tracking-tight">{a.company ?? 'Your finances'}</h1>
          <p className="mt-2 text-sm text-muted-foreground">As of {a.as_of} · {a.summary.invoice_count} invoices and {a.summary.bill_count} bills analyzed</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {explaining && <Badge variant="secondary" className="gap-1"><Loader2 className="size-3 animate-spin" />Writing explanations…</Badge>}
          {a.explained && (
            <Badge variant="secondary" className="gap-1">
              <Sparkles className="size-3" />{a.ai_generated ? 'Explained by Gemini' : a.ai_enabled ? 'Template explanations' : 'Template explanations (no Gemini key)'}
            </Badge>
          )}
          <Button variant="outline" size="sm" onClick={onRefresh} disabled={reloading}>
            <RefreshCw className={cn(reloading && 'animate-spin')} data-icon="inline-start" />Refresh
          </Button>
        </div>
      </div>

      {a.warnings.length > 0 && (
        <div role="alert" className="flex flex-col gap-1 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          {a.warnings.map((w) => <p key={w}>{w}</p>)}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
        <Card>
          <CardHeader><CardTitle className="text-base">Financial health</CardTitle></CardHeader>
          <CardContent className="flex flex-col items-center gap-4">
            <HealthGauge score={a.health.score} label={a.health.label} />
            <Badge variant="outline">{a.health.label}</Badge>
            <ul className="w-full space-y-1.5 text-xs text-muted-foreground">
              {a.health.deductions.length === 0 && <li>No deductions applied.</li>}
              {a.health.deductions.map((d) => (
                <li key={d.reason} className="flex justify-between gap-3"><span>{d.reason}</span><span className="font-medium text-destructive">{d.points}</span></li>
              ))}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex-row items-start justify-between gap-3">
            <div>
              <CardTitle className="text-base">Cash runway forecast</CardTitle>
              <CardDescription>Projected available cash over the next {a.runway.horizon_days} days</CardDescription>
            </div>
            <Badge variant="outline" className={cn(!a.runway.cash_known ? 'text-muted-foreground' : a.runway.runway_day === null ? 'border-emerald-200 text-emerald-700' : 'border-amber-200 text-amber-700')}>
              {!a.runway.cash_known ? 'Cash balance not found' : a.runway.runway_day === null ? 'Stays positive' : `Runs out in ${plural(a.runway.runway_day, 'day')}`}
            </Badge>
          </CardHeader>
          <CardContent>
            {a.runway.cash_known ? (
              <>
                <RunwayChart series={a.runway.series} currency={a.currency} height={220} axes />
                <dl className="mt-4 grid grid-cols-3 gap-3 text-sm">
                  {([['In 30 days', a.runway.balance_30], ['In 60 days', a.runway.balance_60], ['In 90 days', a.runway.balance_90]] as const).map(([k, v]) => (
                    <div key={k} className="rounded-xl bg-muted/40 p-3">
                      <dt className="text-xs text-muted-foreground">{k}</dt>
                      <dd className={cn('font-semibold', (v ?? 0) < 0 && 'text-destructive')}>{v === null ? '—' : money(v)}</dd>
                    </div>
                  ))}
                </dl>
              </>
            ) : (
              <p className="flex h-40 items-center rounded-xl border border-dashed bg-muted/20 p-4 text-sm text-muted-foreground">
                No bank balance was found in the connected data, so a cash runway can&apos;t be projected. Everything else on this page doesn&apos;t depend on it.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Stat label="Cash balance" value={a.runway.cash_known ? money(a.summary.cash_balance) : 'Not found'} />
        <Stat label="Open receivables" value={money(a.summary.open_receivables)} />
        <Stat label="Overdue receivables" value={money(a.summary.overdue_receivables)} tone={a.summary.overdue_receivables > 0 ? 'bad' : undefined} />
        <Stat label="Open payables" value={money(a.summary.open_payables)} />
        <Stat label="Amount at risk" value={money(a.summary.total_amount_at_risk)} tone={a.summary.total_amount_at_risk > 0 ? 'bad' : undefined} />
      </div>

      <section aria-labelledby="findings-heading">
        <h2 id="findings-heading" className="text-xl font-semibold tracking-tight">What needs attention</h2>
        <p className="mt-1 text-sm text-muted-foreground">Every figure is computed from your records; the evidence IDs point to the exact documents.</p>
        {a.findings.length === 0 ? (
          <Card className="mt-4"><CardContent className="flex items-center gap-3 p-5 text-sm"><ShieldCheck className="size-5 text-emerald-600" />No risks detected in the connected data.</CardContent></Card>
        ) : (
          <ul className="mt-4 grid gap-3">
            {a.findings.map((f) => (
              <li key={f.id}>
                <Card>
                  <CardContent className="flex flex-col gap-3 p-5">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className={cn('capitalize', severityStyle[f.severity])}>{f.severity}</Badge>
                        <h3 className="font-semibold">{f.title}</h3>
                      </div>
                      <span className="text-sm font-semibold">{money(f.amount_at_risk)} <span className="font-normal text-muted-foreground">at risk</span></span>
                    </div>
                    {f.explanation ? <p className="text-sm leading-6 text-muted-foreground">{f.explanation}</p> : explaining && <p className="text-sm text-muted-foreground">Writing explanation…</p>}
                    {f.action && (
                      <p className="flex items-start gap-2 rounded-xl bg-primary/5 p-3 text-sm"><Lightbulb className="mt-0.5 size-4 shrink-0 text-primary" /><span><span className="font-medium">Do this: </span>{f.action}</span></p>
                    )}
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs text-muted-foreground">Evidence:</span>
                      {f.evidence.map((id) => <Badge key={id} variant="secondary" className="font-mono text-xs">{id}</Badge>)}
                      {f.type === 'late_payer' && f.evidence.slice(0, 3).map((id) => (
                        <Button key={id} variant="outline" size="xs" disabled={drafting === id} onClick={() => openDraft(id)}>
                          {drafting === id ? <Loader2 className="animate-spin" data-icon="inline-start" /> : <Mail data-icon="inline-start" />}Draft reminder for {id}
                        </Button>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              </li>
            ))}
          </ul>
        )}
      </section>


      <EmailDialog draft={draft} onClose={() => setDraft(null)} />
    </div>
  )
}

function EmailDialog({ draft, onClose }: { draft: EmailDraft | null; onClose: () => void }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    if (!draft) return
    try {
      await navigator.clipboard.writeText(`Subject: ${draft.subject}\n\n${draft.body}`)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error("Couldn't copy to the clipboard")
    }
  }
  return (
    <Dialog open={draft !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Collection email draft</DialogTitle>
          <DialogDescription>
            {draft ? `For ${draft.invoice.customer}, invoice ${draft.invoice.id}. ` : ''}This is a draft only. Nothing is sent from FinSafe AI.
          </DialogDescription>
        </DialogHeader>
        {draft && (
          <div className="flex flex-col gap-3 text-sm">
            <Badge variant="secondary" className="w-fit">{draft.ai_generated ? 'Written by Gemini' : 'Template'}</Badge>
            <p><span className="font-medium">Subject: </span>{draft.subject}</p>
            <p className="max-h-72 overflow-y-auto whitespace-pre-wrap rounded-xl bg-muted p-4 leading-6">{draft.body}</p>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Close</Button>
          <Button onClick={copy}>{copied ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}{copied ? 'Copied' : 'Copy draft'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
