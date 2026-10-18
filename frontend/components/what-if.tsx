'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ArrowRight, Loader2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Notice } from '@/components/notice'
import { RunwayChart } from '@/components/live-preview'
import { ApiError, getAnalysis, getWhatIfOptions, runWhatIf, type Analysis, type WhatIfOptions, type WhatIfResult } from '@/lib/api'
import { CustomerDefaultTool, EarlyDiscountTool, PaymentTermsTool } from '@/components/what-if-tools'
import { formatMoney } from '@/lib/format'

const errorMessage = (e: unknown) => (e instanceof Error ? e.message : 'Something went wrong')

const TOOLS = [
  { id: 'runway', label: 'Cash runway', blurb: 'Collect specific invoices early or delay specific bills, and see how your runway changes.' },
  { id: 'discount', label: 'Early-payment discount', blurb: 'What a "pay within N days for X% off" offer really costs you per year.' },
  { id: 'terms', label: 'Payment terms', blurb: 'How much cash you free by collecting faster or paying vendors later.' },
  { id: 'default', label: 'Customer default', blurb: 'Stress-test your cash against customers who never pay.' },
] as const
type ToolId = (typeof TOOLS)[number]['id']

type State = { kind: 'loading' } | { kind: 'empty' } | { kind: 'error'; message: string } | { kind: 'ready'; analysis: Analysis; options: WhatIfOptions }

export function WhatIfPage() {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [tool, setTool] = useState<ToolId>('runway')

  const load = async () => {
    try {
      const [analysis, options] = await Promise.all([getAnalysis(false), getWhatIfOptions()])
      setState({ kind: 'ready', analysis, options })
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setState({ kind: 'empty' })
      else setState({ kind: 'error', message: errorMessage(e) })
    }
  }

  useEffect(() => {
    load()
  }, [])

  return (
    <main className="mx-auto max-w-4xl px-5 py-10 lg:px-8 lg:py-14">
      {state.kind === 'loading' && (
        <div className="flex items-center justify-center gap-2 py-32 text-muted-foreground"><Loader2 className="size-5 animate-spin" />Loading your data…</div>
      )}
      {state.kind === 'empty' && (
        <Notice title="No data to simulate yet" body="Connect your accounting system first. Scenarios are run on your own invoices and bills.">
          <Link href="/integrations" className={buttonVariants({ size: 'lg' })}>Connect your data <ArrowRight data-icon="inline-end" /></Link>
        </Notice>
      )}
      {state.kind === 'error' && (
        <Notice title="Couldn't load the what-if tools" body={state.message}>
          <Button variant="outline" onClick={() => { setState({ kind: 'loading' }); load() }}>Retry</Button>
        </Notice>
      )}
      {state.kind === 'ready' && (
        <div className="flex flex-col gap-6">
          <div>
            <Badge variant="outline" className="mb-3 border-primary/20 bg-primary/5 text-primary">What-if</Badge>
            <h1 className="text-3xl font-semibold tracking-tight">Test a decision before you make it</h1>
            <p className="mt-2 text-sm text-muted-foreground">Four scenario tools that run on your own invoices and bills. Nothing here changes your books.</p>
          </div>
          <div className="flex flex-wrap gap-2" role="group" aria-label="What-if tools">
            {TOOLS.map((t) => (
              <Button key={t.id} variant={tool === t.id ? 'default' : 'outline'} aria-pressed={tool === t.id} onClick={() => setTool(t.id)}>{t.label}</Button>
            ))}
          </div>
          <p className="text-sm text-muted-foreground">{TOOLS.find((t) => t.id === tool)?.blurb}</p>
          {tool === 'runway' && <WhatIf a={state.analysis} />}
          {tool === 'discount' && <EarlyDiscountTool options={state.options} />}
          {tool === 'terms' && <PaymentTermsTool options={state.options} />}
          {tool === 'default' && <CustomerDefaultTool options={state.options} />}
        </div>
      )}
    </main>
  )
}

function WhatIf({ a }: { a: Analysis }) {
  const [collect, setCollect] = useState<string[]>([])
  const [delay, setDelay] = useState<string[]>([])
  const [days, setDays] = useState('')
  const [result, setResult] = useState<WhatIfResult | null>(null)
  const [busy, setBusy] = useState(false)
  const run = useRef(0)

  const invoices = a.runway.drivers.filter((d) => d.kind === 'invoice')
  const bills = a.runway.drivers.filter((d) => d.kind === 'bill')
  const delayDays = Number.parseInt(days, 10)
  const needsDays = delay.length > 0 && !(delayDays > 0)

  const toggle = (list: string[], set: (v: string[]) => void, id: string) => set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id])

  useEffect(() => {
    const ticket = ++run.current
    if ((!collect.length && !delay.length) || needsDays) {
      setResult(null)
      return
    }
    setBusy(true)
    const timer = setTimeout(async () => {
      try {
        const res = await runWhatIf({ collect_invoice_ids: collect, delay_bill_ids: delay, delay_days: delayDays || 0 })
        if (ticket === run.current) setResult(res)
      } catch (e) {
        if (ticket === run.current) toast.error("Couldn't run the simulation", { description: errorMessage(e) })
      }
      if (ticket === run.current) setBusy(false)
    }, 300)
    return () => clearTimeout(timer)
  }, [collect, delay, delayDays, needsDays])

  const money = (n: number) => formatMoney(n, a.currency)
  const runwayText = (d: number | null) => (d === null ? 'no shortfall' : `day ${d}`)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Cash runway simulator</CardTitle>
        <CardDescription>Pick the biggest upcoming movements and see how the runway changes.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {!a.runway.cash_known ? (
          <p className="text-sm text-muted-foreground">Scenarios need a cash balance, and none was found in the connected data.</p>
        ) : a.runway.drivers.length === 0 ? (
          <p className="text-sm text-muted-foreground">No upcoming invoices or bills in the forecast window.</p>
        ) : (
          <>
            {invoices.length > 0 && (
              <fieldset className="flex flex-col gap-2">
                <legend className="mb-1 text-sm font-medium">Collect these invoices today</legend>
                {invoices.map((d) => (
                  <label key={d.id} className="flex items-center gap-3 rounded-xl border p-3 text-sm">
                    <Checkbox checked={collect.includes(d.id)} onCheckedChange={() => toggle(collect, setCollect, d.id)} />
                    <span className="flex-1">{d.party} <span className="font-mono text-xs text-muted-foreground">{d.id}</span></span>
                    <span className="font-medium">{money(d.amount)}</span>
                  </label>
                ))}
              </fieldset>
            )}
            {bills.length > 0 && (
              <fieldset className="flex flex-col gap-2">
                <legend className="mb-1 text-sm font-medium">Delay these bills</legend>
                {bills.map((d) => (
                  <label key={d.id} className="flex items-center gap-3 rounded-xl border p-3 text-sm">
                    <Checkbox checked={delay.includes(d.id)} onCheckedChange={() => toggle(delay, setDelay, d.id)} />
                    <span className="flex-1">{d.party} <span className="font-mono text-xs text-muted-foreground">{d.id}</span></span>
                    <span className="font-medium">{money(Math.abs(d.amount))}</span>
                  </label>
                ))}
                <div className="flex items-center gap-3 pt-1">
                  <Label htmlFor="delay-days" className="text-sm">Delay by (days)</Label>
                  <Input id="delay-days" type="number" min={1} inputMode="numeric" className="w-28" placeholder="e.g. 14" value={days} onChange={(e) => setDays(e.target.value)} />
                </div>
                {needsDays && <p className="text-xs text-muted-foreground">Enter how many days to delay the selected bills.</p>}
              </fieldset>
            )}
          </>
        )}
        {busy && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Recalculating…</p>}
        {result && !busy && (
          <div className="flex flex-col gap-3" aria-live="polite">
            <RunwayChart series={result.before.series} compare={result.after.series} currency={a.currency} height={170} axes />
            <p className="rounded-xl bg-primary/5 p-3 text-sm">
              Runway moves from <strong>{runwayText(result.runway_before)}</strong> to <strong>{runwayText(result.runway_after)}</strong>
              {result.no_longer_runs_out && ' — the shortfall is avoided.'}
              {result.runway_gain_days !== null && result.runway_gain_days !== 0 && ` (${result.runway_gain_days > 0 ? '+' : ''}${result.runway_gain_days} days).`}
              {result.cash_collected > 0 && ` Collects ${money(result.cash_collected)}.`}
              {result.payments_delayed > 0 && ` Postpones ${money(result.payments_delayed)}.`}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
