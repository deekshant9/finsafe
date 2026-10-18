'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, ArrowDown, ArrowRight, ArrowUp, Loader2, RefreshCw } from 'lucide-react'
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip as ChartTooltip, XAxis } from 'recharts'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Notice } from '@/components/notice'
import { Progress } from '@/components/ui/progress'
import { ApiError, getInsights, type Insights, type Metric } from '@/lib/api'
import { formatCompact, formatMoney } from '@/lib/format'
import { cn } from '@/lib/utils'

const SOURCE_NAMES = { zoho: 'Zoho Books', netsuite: 'Oracle NetSuite', odoo: 'Odoo' } as const

type State = { kind: 'loading' } | { kind: 'empty' } | { kind: 'error'; message: string } | { kind: 'ready'; data: Insights }

export function InsightsPage() {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async () => {
    try {
      setState({ kind: 'ready', data: await getInsights() })
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setState({ kind: 'empty' })
      else setState({ kind: 'error', message: e instanceof Error ? e.message : 'Something went wrong' })
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return (
    <>
      <main className="mx-auto max-w-6xl px-5 py-10 lg:px-8 lg:py-14">
        {state.kind === 'loading' && (
          <div className="flex items-center justify-center gap-2 py-32 text-muted-foreground"><Loader2 className="size-5 animate-spin" />Calculating your metrics…</div>
        )}
        {state.kind === 'empty' && (
          <Notice title="No data to analyze yet" body="Connect any one of Zoho Books, NetSuite or Odoo. Insights are calculated from whichever platforms you connect, and you don't need all of them.">
            <Link href="/integrations" className={buttonVariants({ size: 'lg' })}>Connect your data <ArrowRight data-icon="inline-end" /></Link>
          </Notice>
        )}
        {state.kind === 'error' && (
          <Notice title="Couldn't load your insights" body={state.message}>
            <Button variant="outline" onClick={() => { setState({ kind: 'loading' }); load() }}>Retry</Button>
          </Notice>
        )}
        {state.kind === 'ready' && (
          <Content
            data={state.data}
            refreshing={refreshing}
            onRefresh={async () => {
              setRefreshing(true)
              await load()
              setRefreshing(false)
            }}
          />
        )}
      </main>
    </>
  )
}

function Content({ data, refreshing, onRefresh }: { data: Insights; refreshing: boolean; onRefresh: () => void }) {
  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <Badge variant="outline" className="mb-3 border-primary/20 bg-primary/5 text-primary">Insights</Badge>
          <h1 className="text-3xl font-semibold tracking-tight">{data.company ?? 'Your business'} at a glance</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            Ten metrics calculated from your records. They work with whichever platforms are connected, and a metric that needs data you don&apos;t have yet says what&apos;s missing.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {data.sources.map((id) => <Badge key={id} variant="secondary">{SOURCE_NAMES[id]}</Badge>)}
          <Badge variant="outline">{data.available_count} of {data.metrics.length} available</Badge>
          <Button variant="outline" size="sm" onClick={onRefresh} disabled={refreshing}>
            <RefreshCw className={cn(refreshing && 'animate-spin')} data-icon="inline-start" />Refresh
          </Button>
        </div>
      </div>
      <ul className="grid gap-4 md:grid-cols-2">
        {data.metrics.map((m) => (
          <li key={m.id}><MetricCard m={m} currency={data.currency} /></li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">As of {data.as_of}. Figures are decision support, not financial advice.</p>
    </div>
  )
}

function formatValue(m: Metric, currency: string): string {
  if (m.value === null) return '—'
  const trimmed = (n: number, digits = 1) => Number(n.toFixed(digits)).toString()
  switch (m.unit) {
    case 'days': return `${trimmed(m.value)} days`
    case 'percent': return `${trimmed(m.value)}%`
    case 'ratio': return `${trimmed(m.value, 2)}×`
    case 'currency': return formatMoney(m.value, currency)
  }
}

function MetricCard({ m, currency }: { m: Metric; currency: string }) {
  if (!m.available) {
    return (
      <Card className="h-full border-dashed bg-muted/20">
        <CardHeader>
          <CardTitle className="text-base">{m.title}</CardTitle>
          <CardDescription>{m.description}</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="rounded-xl bg-muted/50 p-3 text-sm text-muted-foreground"><span className="font-medium text-foreground">Not enough data yet. </span>{m.reason}</p>
        </CardContent>
      </Card>
    )
  }
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="text-base">{m.title}</CardTitle>
        <CardDescription>{m.description}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap items-baseline gap-3">
          <span className="text-3xl font-semibold tracking-tight">{formatValue(m, currency)}</span>
          {m.change_pct !== null && (
            <Badge variant="outline" className="gap-1" aria-label={`${m.change_pct >= 0 ? 'Up' : 'Down'} ${Math.abs(m.change_pct)} percent versus the month before`}>
              {m.change_pct >= 0 ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />}
              {Math.abs(m.change_pct)}% vs prior month
            </Badge>
          )}
        </div>
        {m.note && <p className="text-sm text-muted-foreground">{m.note}</p>}
        <Visual m={m} currency={currency} />
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none font-medium">How this is calculated</summary>
          <p className="mt-1 font-mono leading-5">{m.formula}</p>
        </details>
      </CardContent>
    </Card>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  )
}

function BarList({ rows, currency }: { rows: { label: string; amount: number; share: number }[]; currency: string }) {
  return (
    <ul className="flex flex-col gap-2.5">
      {rows.map((r) => (
        <li key={r.label} className="text-sm">
          <div className="mb-1 flex justify-between gap-3"><span className="truncate">{r.label}</span><span className="shrink-0 text-muted-foreground">{formatCompact(r.amount, currency)} · {Number(r.share.toFixed(1))}%</span></div>
          <div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(r.share, 100)}%` }} /></div>
        </li>
      ))}
    </ul>
  )
}

function Visual({ m, currency }: { m: Metric; currency: string }) {
  const d = m.detail
  const money = (n: number) => formatMoney(n, currency)

  switch (m.id) {
    case 'dso':
      return <dl className="flex flex-col gap-1.5"><Row label="Open receivables" value={money(d.open_receivables)} /><Row label={`Invoiced, last 90 days`} value={money(d.invoiced_recent)} /></dl>
    case 'dpo':
      return <dl className="flex flex-col gap-1.5"><Row label="Open payables" value={money(d.open_payables)} /><Row label="Billed, last 90 days" value={money(d.billed_recent)} /></dl>
    case 'on_time_rate':
      return (
        <div className="flex flex-col gap-3">
          <Progress value={m.value ?? 0} />
          <dl className="flex flex-col gap-1.5">
            <Row label="Paid on time" value={`${d.on_time} of ${d.paid_invoices} invoices`} />
            <Row label="Paid late" value={`${d.late}${d.late ? `, ${d.avg_days_late} days late on average` : ''}`} />
          </dl>
        </div>
      )
    case 'days_to_paid':
      return <dl className="flex flex-col gap-1.5"><Row label="Median" value={`${d.median} days`} /><Row label="Fastest" value={`${d.fastest} days`} /><Row label="Slowest" value={`${d.slowest} days`} /><Row label="Invoices counted" value={String(d.paid_invoices)} /></dl>
    case 'receivables_aging': {
      const max = Math.max(...d.buckets.map((b: { amount: number }) => b.amount), 0)
      return (
        <ul className="flex flex-col gap-2.5">
          {d.buckets.map((b: { label: string; amount: number; count: number }) => (
            <li key={b.label} className="text-sm">
              <div className="mb-1 flex justify-between gap-3"><span>{b.label}</span><span className="text-muted-foreground">{money(b.amount)} · {b.count} {b.count === 1 ? 'invoice' : 'invoices'}</span></div>
              <div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: max > 0 ? `${(b.amount / max) * 100}%` : '0%' }} /></div>
            </li>
          ))}
        </ul>
      )
    }
    case 'customer_concentration':
    case 'vendor_concentration':
      return <BarList currency={currency} rows={d.top.map((t: { name: string; amount: number; share: number }) => ({ label: t.name, amount: t.amount, share: t.share }))} />
    case 'revenue_trend':
    case 'spend_trend':
      return (
        <div className="flex flex-col gap-2">
          <div className="h-36 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={d.series} margin={{ left: 0, right: 0, top: 6, bottom: 0 }}>
                <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fontSize: 11, fill: '#94a3b8' }} />
                <ChartTooltip cursor={{ fill: 'var(--muted)' }} contentStyle={{ borderRadius: 12, border: '1px solid var(--border)', fontSize: 12 }}
                  formatter={(v) => [money(Number(v ?? 0)), m.id === 'revenue_trend' ? 'Invoiced' : 'Billed']}
                  labelFormatter={(_, items) => `${items?.[0]?.payload?.month ?? ''}${items?.[0]?.payload?.partial ? ' (in progress)' : ''}`} />
                <Bar dataKey="amount" radius={[6, 6, 0, 0]}>
                  {d.series.map((s: { month: string; partial: boolean }) => <Cell key={s.month} fill="#2f4bff" fillOpacity={s.partial ? 0.35 : 1} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <Row label="Average per full month" value={money(d.average_per_month)} />
        </div>
      )
    case 'liquidity_ratio':
      return (
        <dl className="flex flex-col gap-1.5">
          <Row label="Cash balance" value={money(d.cash)} />
          <Row label="Open receivables" value={money(d.open_receivables)} />
          <Row label="Open payables" value={money(d.open_payables)} />
          <Row label="Cash alone covers" value={`${Number(d.cash_coverage.toFixed(2))}× of payables`} />
        </dl>
      )
    default:
      return null
  }
}
