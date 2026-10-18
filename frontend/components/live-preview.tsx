'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { AlertTriangle, ShieldCheck, TrendingDown } from 'lucide-react'
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts'
import { Badge } from '@/components/ui/badge'
import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { getAnalysis, type Analysis, type RunwayPoint } from '@/lib/api'
import { formatCompact, formatMoney, plural } from '@/lib/format'
import { cn } from '@/lib/utils'

const toneByLabel: Record<string, string> = { Healthy: '#10b981', Watch: '#f59e0b', 'At risk': '#ef4444' }

/** Circular conic gauge. `score` null = nothing to show yet (empty ring, no number). */
export function HealthGauge({ score, label }: { score: number | null; label?: string }) {
  const pct = score ?? 0
  const color = (label && toneByLabel[label]) || '#94a3b8'
  return (
    <div className="relative flex size-32 items-center justify-center" role="img" aria-label={score === null ? 'Financial health unavailable' : `Financial health ${score} out of 100`}>
      <div
        className="absolute inset-0 rounded-full"
        style={{ background: `conic-gradient(${color} 0 ${pct}%, var(--muted) ${pct}% 100%)`, mask: 'radial-gradient(farthest-side, transparent calc(100% - 10px), #000 0)', WebkitMask: 'radial-gradient(farthest-side, transparent calc(100% - 10px), #000 0)' }}
      />
      <div className="text-center">
        <div className="text-3xl font-bold tracking-tight">{score === null ? '—' : score}</div>
        <div className="text-[10px] font-medium text-muted-foreground">/100 HEALTH</div>
      </div>
    </div>
  )
}

/** Cash runway area chart. `compare` overlays a second projection (what-if). */
export function RunwayChart({ series, compare, currency, height = 128, axes = false }: { series: RunwayPoint[]; compare?: RunwayPoint[]; currency: string; height?: number; axes?: boolean }) {
  const data = series.map((p, i) => ({ day: p.day, date: p.date, balance: p.balance, scenario: compare?.[i]?.balance }))
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ left: axes ? 8 : -25, right: 8, top: 6, bottom: 0 }}>
          <defs>
            <linearGradient id="runway-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#2f4bff" stopOpacity={0.25} />
              <stop offset="100%" stopColor="#2f4bff" stopOpacity={0} />
            </linearGradient>
          </defs>
          {axes && <CartesianGrid vertical={false} stroke="var(--border)" />}
          <XAxis dataKey="day" axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: '#94a3b8' }} tickFormatter={(d) => (d === 0 ? 'Now' : `${d}d`)} minTickGap={24} />
          <YAxis hide={!axes} axisLine={false} tickLine={false} width={axes ? 64 : 0} tick={{ fontSize: 10, fill: '#94a3b8' }} tickFormatter={(v) => formatCompact(v, currency)} />
          <ReferenceLine y={0} stroke="#ef4444" strokeDasharray="4 4" />
          <ChartTooltip
            contentStyle={{ borderRadius: 12, border: '1px solid var(--border)', fontSize: 12 }}
            labelFormatter={(d, items) => `${d === 0 ? 'Today' : `In ${d} days`}${items?.[0]?.payload?.date ? ` · ${items[0].payload.date}` : ''}`}
            formatter={(v, name) => [formatMoney(Number(v ?? 0), currency), name === 'scenario' ? 'With changes' : 'Projected cash']}
          />
          <Area type="monotone" dataKey="balance" stroke="#2f4bff" strokeWidth={2} fill="url(#runway-fill)" />
          {compare && <Area type="monotone" dataKey="scenario" stroke="#10b981" strokeWidth={2} strokeDasharray="5 3" fill="none" />}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

type Load = { state: 'loading' } | { state: 'empty' } | { state: 'error' } | { state: 'ready'; analysis: Analysis }

/** Home hero card. Only ever shows numbers from the connected account. */
export function ProductPreview() {
  const [load, setLoad] = useState<Load>({ state: 'loading' })

  useEffect(() => {
    getAnalysis(false)
      .then((analysis) => setLoad({ state: 'ready', analysis }))
      .catch((e) => setLoad({ state: e?.status === 409 ? 'empty' : 'error' }))
  }, [])

  const a = load.state === 'ready' ? load.analysis : null
  const chips = (a?.findings ?? []).filter((f) => f.type !== 'cash_shortfall').slice(0, 2)

  return (
    <Card className="overflow-hidden border-primary/10 bg-card shadow-2xl shadow-primary/10">
      <CardHeader className="flex flex-row items-center justify-between gap-3 border-b bg-muted/20 px-5 py-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <span className={cn('size-2 shrink-0 rounded-full', a ? 'bg-emerald-500' : 'bg-slate-300')} /> Financial overview
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">{a ? `${a.company ?? 'Connected account'} · as of ${a.as_of}` : 'Waiting for your data'}</div>
        </div>
        <Badge variant="secondary" className="shrink-0 gap-1 text-emerald-700"><ShieldCheck className="size-3" /> Safe mode</Badge>
      </CardHeader>
      <CardContent className="grid gap-5 p-5 sm:grid-cols-[auto_1fr]">
        <div className="flex items-center gap-4 sm:block">
          <HealthGauge score={a?.health.score ?? null} label={a?.health.label} />
          <div className="mt-2 text-center text-xs font-medium text-muted-foreground">{a ? a.health.label : 'Financial health'}</div>
        </div>
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-end justify-between gap-x-3 gap-y-1">
            <div>
              <p className="text-sm font-semibold">Cash runway forecast</p>
              <p className="text-xs text-muted-foreground">Projected available cash</p>
            </div>
            {a && a.runway.cash_known && (
              <span className={cn('text-xs font-semibold', a.runway.runway_day === null ? 'text-emerald-600' : 'text-amber-600')}>
                {a.runway.runway_day === null ? `Stays positive for ${plural(a.runway.horizon_days, 'day')}` : `Runs out in ${plural(a.runway.runway_day, 'day')}`}
              </span>
            )}
          </div>
          {a && !a.runway.cash_known ? (
            <p className="flex h-32 items-center rounded-xl border border-dashed bg-muted/20 p-4 text-sm text-muted-foreground">
              No bank balance was found in the connected data, so a cash runway can&apos;t be shown.
            </p>
          ) : a ? (
            <>
              <RunwayChart series={a.runway.series} currency={a.currency} />
              {chips.length > 0 && (
                <ul className="mt-3 flex flex-col items-start gap-2">
                  {chips.map((f) => (
                    <li key={f.id} className="max-w-full">
                      <Badge variant="outline" title={`${f.title} · ${formatMoney(f.amount_at_risk, a.currency)}`}
                        className={cn('h-auto max-w-full items-start justify-start gap-1.5 whitespace-normal py-1 text-left leading-snug', f.severity === 'high' ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300' : 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300')}>
                        {f.type === 'vendor_price_spike' ? <TrendingDown className="mt-0.5 size-3 shrink-0" /> : <AlertTriangle className="mt-0.5 size-3 shrink-0" />}
                        <span className="min-w-0 break-words">{f.title} <span className="whitespace-nowrap font-semibold">· {formatCompact(f.amount_at_risk, a.currency)}</span></span>
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <div className="flex h-32 flex-col items-start justify-center gap-3 rounded-xl border border-dashed bg-muted/20 p-4 text-sm text-muted-foreground">
              <p>
                {load.state === 'loading' && 'Loading…'}
                {load.state === 'empty' && 'Connect your accounting system to see your health score, cash runway and risks here.'}
                {load.state === 'error' && "Can't reach the FinSafe backend right now."}
              </p>
              {load.state === 'empty' && <Link href="/integrations" className={buttonVariants({ size: 'sm' })}>Connect your data</Link>}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
