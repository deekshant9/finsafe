'use client'

import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RunwayChart } from '@/components/live-preview'
import {
  runCustomerDefault,
  runEarlyDiscount,
  runPaymentTerms,
  type CustomerDefaultResult,
  type EarlyDiscountResult,
  type PaymentTermsResult,
  type RunwayEffect,
  type WhatIfOptions,
} from '@/lib/api'
import { formatMoney, plural } from '@/lib/format'
import { cn } from '@/lib/utils'

/** Runs `run` shortly after the inputs change (when `enabled`), ignoring answers that arrive out of order. */
function useScenario<T>(enabled: boolean, deps: unknown[], run: () => Promise<T>) {
  const [result, setResult] = useState<T | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ticket = useRef(0)

  useEffect(() => {
    const mine = ++ticket.current
    if (!enabled) {
      setResult(null)
      setError(null)
      setBusy(false)
      return
    }
    setBusy(true)
    const timer = setTimeout(async () => {
      try {
        const res = await run()
        if (mine === ticket.current) {
          setResult(res)
          setError(null)
        }
      } catch (e) {
        if (mine === ticket.current) {
          setResult(null)
          setError(e instanceof Error ? e.message : 'Something went wrong')
        }
      }
      if (mine === ticket.current) setBusy(false)
    }, 300)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps])

  return { result, busy, error }
}

const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id])

function Tile({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: 'bad' | 'good' }) {
  return (
    <div className="rounded-xl bg-muted/40 p-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn('mt-0.5 text-lg font-semibold tracking-tight', tone === 'bad' && 'text-destructive', tone === 'good' && 'text-emerald-600')}>{value}</dd>
      {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

function Status({ busy, error }: { busy: boolean; error: string | null }) {
  if (busy) return <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Calculating…</p>
  if (error) return <p role="alert" className="flex items-start gap-2 rounded-xl bg-destructive/10 p-3 text-sm text-destructive"><AlertTriangle className="mt-0.5 size-4 shrink-0" />{error}</p>
  return null
}

const dayText = (d: number | null) => (d === null ? 'no shortfall' : `day ${d}`)

/** Before/after cash runway for a scenario; says so plainly when no cash balance exists to project from. */
function RunwayEffectView({ effect, currency }: { effect: RunwayEffect | null; currency: string }) {
  if (!effect) {
    return <p className="rounded-xl border border-dashed bg-muted/20 p-3 text-sm text-muted-foreground">Runway impact isn&apos;t shown because no bank balance was found in the connected data.</p>
  }
  const verdict = effect.no_longer_runs_out
    ? 'The cash shortfall is avoided.'
    : effect.newly_runs_out
      ? 'This creates a cash shortfall that does not exist today.'
      : effect.runway_gain_days
        ? `${effect.runway_gain_days > 0 ? 'Adds' : 'Removes'} ${plural(Math.abs(effect.runway_gain_days), 'day')} of runway.`
        : 'The runway does not change.'
  return (
    <div className="flex flex-col gap-3" aria-live="polite">
      <RunwayChart series={effect.before_series} compare={effect.after_series} currency={currency} height={170} axes />
      <p className="rounded-xl bg-primary/5 p-3 text-sm">
        Runway moves from <strong>{dayText(effect.runway_before)}</strong> to <strong>{dayText(effect.runway_after)}</strong>. {verdict}
        {effect.lowest_balance_before !== null && effect.lowest_balance_after !== null && (
          <> Lowest projected balance: {formatMoney(effect.lowest_balance_before, currency)} → {formatMoney(effect.lowest_balance_after, currency)}.</>
        )}
      </p>
    </div>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>
}

// ── 1. Early-payment discount ──
export function EarlyDiscountTool({ options }: { options: WhatIfOptions }) {
  const [ids, setIds] = useState<string[]>([])
  const [pct, setPct] = useState('')
  const [days, setDays] = useState('')
  const money = (n: number) => formatMoney(n, options.currency)

  const discount = Number(pct)
  const within = Number.parseInt(days, 10)
  const ready = ids.length > 0 && discount > 0 && discount < 100 && within >= 0
  const { result: r, busy, error } = useScenario<EarlyDiscountResult>(ready, [ids, discount, within], () =>
    runEarlyDiscount({ invoice_ids: ids, discount_pct: discount, pay_within_days: within }))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Early-payment discount</CardTitle>
        <CardDescription>Offer customers a discount for paying quickly. See what it really costs per year, and what it does to your runway.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {options.invoices.length === 0 ? <Empty>No open invoices to offer a discount on.</Empty> : (
          <>
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-1 flex w-full items-center justify-between text-sm font-medium">
                Invoices to offer it on
                <span className="flex gap-1">
                  <Button type="button" variant="ghost" size="xs" onClick={() => setIds(options.invoices.map((i) => i.id))}>Select all</Button>
                  <Button type="button" variant="ghost" size="xs" onClick={() => setIds([])}>Clear</Button>
                </span>
              </legend>
              <div className="flex max-h-64 flex-col gap-2 overflow-y-auto pr-1">
                {options.invoices.map((i) => (
                  <label key={i.id} className="flex items-center gap-3 rounded-xl border p-3 text-sm">
                    <Checkbox checked={ids.includes(i.id)} onCheckedChange={() => setIds(toggle(ids, i.id))} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{i.customer} <span className="font-mono text-xs text-muted-foreground">{i.id}</span></span>
                      <span className="text-xs text-muted-foreground">{i.days_overdue > 0 ? `${plural(i.days_overdue, 'day')} overdue` : `due ${i.due_date}`}</span>
                    </span>
                    <span className="font-medium">{money(i.outstanding)}</span>
                  </label>
                ))}
              </div>
              {options.truncated && <p className="text-xs text-muted-foreground">Showing the largest invoices only.</p>}
            </fieldset>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-2">
                <Label htmlFor="disc-pct">Discount (%)</Label>
                <Input id="disc-pct" type="number" min={0} max={100} step="any" inputMode="decimal" placeholder="e.g. 2" value={pct} onChange={(e) => setPct(e.target.value)} />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="disc-days">If the customer pays within (days from today)</Label>
                <Input id="disc-days" type="number" min={0} inputMode="numeric" placeholder="e.g. 10" value={days} onChange={(e) => setDays(e.target.value)} />
              </div>
            </div>
            {!ready && <Empty>Pick at least one invoice and enter the discount and the payment window.</Empty>}
          </>
        )}
        <Status busy={busy} error={error} />
        {r && !busy && (
          <div className="flex flex-col gap-4">
            <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Tile label="Discount you give up" value={money(r.total_discount_cost)} tone="bad" />
              <Tile label="Cash pulled forward" value={money(r.cash_pulled_forward)} />
              <Tile label="Paid earlier by" value={r.avg_days_accelerated === null ? '—' : plural(Math.round(r.avg_days_accelerated), 'day')} hint="on average" />
              <Tile label="Cost per year" value={r.annualized_cost_pct === null ? '—' : `${r.annualized_cost_pct}%`} hint="of the cash received early" />
            </dl>
            {r.annualized_cost_pct !== null && (
              <p className="text-sm text-muted-foreground">
                Giving up {money(r.total_discount_cost)} to get {money(r.cash_pulled_forward)} sooner is like borrowing at about {r.annualized_cost_pct}% a year. Compare that with what your own borrowing or idle cash costs you.
              </p>
            )}
            {r.discount_given_for_nothing > 0 && (
              <p className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                {money(r.discount_given_for_nothing)} of the discount goes to customers who would pay that quickly anyway: {r.invoices.filter((i) => i.pays_sooner_anyway).map((i) => i.id).join(', ')}.
              </p>
            )}
            <ul className="flex flex-col gap-1.5 text-sm">
              {r.invoices.map((i) => (
                <li key={i.id} className="flex items-center justify-between gap-3 rounded-lg bg-muted/30 px-3 py-2">
                  <span className="min-w-0 truncate">{i.customer} <span className="font-mono text-xs text-muted-foreground">{i.id}</span></span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {i.pays_sooner_anyway ? 'pays sooner anyway' : `${plural(i.days_accelerated, 'day')} earlier · ${i.annualized_cost_pct}%/yr`} · {money(i.discount)} off
                  </span>
                </li>
              ))}
            </ul>
            <RunwayEffectView effect={r.runway} currency={options.currency} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ── 2. Payment terms ──
export function PaymentTermsTool({ options }: { options: WhatIfOptions }) {
  const [faster, setFaster] = useState('')
  const [later, setLater] = useState('')
  const money = (n: number) => formatMoney(n, options.currency)

  const f = faster === '' ? 0 : Number.parseInt(faster, 10)
  const l = later === '' ? 0 : Number.parseInt(later, 10)
  const ready = f >= 0 && l >= 0 && f + l > 0
  const { result: r, busy, error } = useScenario<PaymentTermsResult>(ready, [f, l], () =>
    runPaymentTerms({ collect_days_faster: f, pay_days_later: l }))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Payment terms</CardTitle>
        <CardDescription>How much cash you free by collecting faster, paying vendors later, or both.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-2">
            <Label htmlFor="terms-faster">Customers pay faster by (days)</Label>
            <Input id="terms-faster" type="number" min={0} inputMode="numeric" placeholder="e.g. 5" value={faster} onChange={(e) => setFaster(e.target.value)} />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="terms-later">You pay vendors later by (days)</Label>
            <Input id="terms-later" type="number" min={0} inputMode="numeric" placeholder="e.g. 7" value={later} onChange={(e) => setLater(e.target.value)} />
          </div>
        </div>
        {!ready && <Empty>Enter at least one of the two.</Empty>}
        <Status busy={busy} error={error} />
        {r && !busy && (
          <div className="flex flex-col gap-4">
            <dl className="grid grid-cols-2 gap-3 lg:grid-cols-3">
              <Tile label="Freed from receivables" value={r.cash_freed_from_receivables === null ? '—' : money(r.cash_freed_from_receivables)} />
              <Tile label="Freed from payables" value={r.cash_freed_from_payables === null ? '—' : money(r.cash_freed_from_payables)} />
              <Tile label="Total cash freed" value={money(r.total_cash_freed)} tone="good" hint="one-off" />
              {r.dso && <Tile label="Days sales outstanding" value={`${r.dso.before} → ${r.dso.after}`} />}
              {r.dpo && <Tile label="Days payables outstanding" value={`${r.dpo.before} → ${r.dpo.after}`} />}
            </dl>
            <p className="text-sm text-muted-foreground">
              Based on your last {r.basis_days} days: about {money(r.daily_invoiced)} invoiced and {money(r.daily_billed)} billed per day. Each day gained is worth one day of that flow.
            </p>
            {r.notes.map((n) => <p key={n} className="text-sm text-amber-700 dark:text-amber-300">{n}</p>)}
            <RunwayEffectView effect={r.runway} currency={options.currency} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ── 3. Customer default stress test ──
export function CustomerDefaultTool({ options }: { options: WhatIfOptions }) {
  const [names, setNames] = useState<string[]>([])
  const money = (n: number) => formatMoney(n, options.currency)
  const { result: r, busy, error } = useScenario<CustomerDefaultResult>(names.length > 0, [names], () => runCustomerDefault({ customers: names }))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Customer default stress test</CardTitle>
        <CardDescription>What if a customer never pays what they owe? See how exposed your cash is to your biggest debtors.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {options.customers.length === 0 ? <Empty>No customers with open invoices.</Empty> : (
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">Customers who don&apos;t pay</legend>
            <div className="flex max-h-64 flex-col gap-2 overflow-y-auto pr-1">
              {options.customers.map((c) => (
                <label key={c.name} className="flex items-center gap-3 rounded-xl border p-3 text-sm">
                  <Checkbox checked={names.includes(c.name)} onCheckedChange={() => setNames(toggle(names, c.name))} />
                  <span className="min-w-0 flex-1 truncate">{c.name} <span className="text-xs text-muted-foreground">· {plural(c.invoice_count, 'invoice')}</span></span>
                  <span className="font-medium">{money(c.outstanding)}</span>
                </label>
              ))}
            </div>
            {options.truncated && <p className="text-xs text-muted-foreground">Showing the customers who owe the most.</p>}
          </fieldset>
        )}
        {names.length === 0 && options.customers.length > 0 && <Empty>Pick one or more customers.</Empty>}
        <Status busy={busy} error={error} />
        {r && !busy && (
          <div className="flex flex-col gap-4">
            <dl className="grid grid-cols-2 gap-3 lg:grid-cols-3">
              <Tile label="Receivables lost" value={money(r.receivables_lost)} tone="bad" />
              <Tile label="Share of what you're owed" value={`${r.share_of_open_receivables_pct}%`} />
              <Tile label="Still expected" value={money(r.open_receivables_after)} />
            </dl>
            <ul className="flex flex-col gap-1.5 text-sm">
              {r.customers.map((c) => (
                <li key={c.name} className="flex items-center justify-between gap-3 rounded-lg bg-muted/30 px-3 py-2">
                  <span className="min-w-0 truncate">{c.name}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{money(c.outstanding)} owed · {money(c.overdue)} overdue</span>
                </li>
              ))}
            </ul>
            <RunwayEffectView effect={r.runway} currency={options.currency} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}
