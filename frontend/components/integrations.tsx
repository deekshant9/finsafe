'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { AlertTriangle, ArrowRight, Check, CheckCircle2, KeyRound, RefreshCw, ShieldCheck, X } from 'lucide-react'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import {
  connectConnector,
  disconnectConnector,
  getConnectors,
  syncConnector,
  type ConnectorId,
  type ConnectorState,
  type ConnectorStatus,
} from '@/lib/api'
import { timeAgo } from '@/lib/format'
import { cn } from '@/lib/utils'

const CONNECTOR_IDS: ConnectorId[] = ['zoho', 'netsuite', 'odoo']

const connectorInfo = {
  zoho: { name: 'Zoho Books', initials: 'ZB', description: 'Cloud accounting for invoices, bills and expenses.', auth: 'Zoho Books OAuth 2.0' },
  netsuite: { name: 'Oracle NetSuite', initials: 'NS', description: 'Enterprise ERP for finance, billing and procurement.', auth: 'Oracle NetSuite OAuth 2.0' },
  odoo: { name: 'Odoo', initials: 'OD', description: 'Open-source ERP with accounting, sales and purchasing.', auth: 'Odoo API key' },
} as const

const errorMessage = (e: unknown) => (e instanceof Error ? e.message : 'Something went wrong')

function StatusBadge({ status }: { status: ConnectorStatus }) {
  const label = { connected: 'Connected', connecting: 'Connecting…', error: 'Error', disconnected: 'Not connected' }[status]
  return (
    <Badge
      variant={status === 'connected' ? 'default' : status === 'error' ? 'destructive' : status === 'connecting' ? 'secondary' : 'outline'}
      className={cn('gap-1', status === 'connected' && 'bg-emerald-600 hover:bg-emerald-600', status === 'connecting' && 'text-amber-700')}
    >
      <span className={cn('size-1.5 rounded-full', status === 'connected' ? 'bg-white' : status === 'error' ? 'bg-destructive' : status === 'connecting' ? 'animate-pulse bg-amber-500' : 'bg-slate-300')} />
      {label}
    </Badge>
  )
}

const EMPTY_FIELDS = { url: '', database: '', apiKey: '', login: '', account: '' }

function ConnectDialog({ id, retry, onAuthorize }: { id: ConnectorId; retry?: boolean; onAuthorize: (fields: typeof EMPTY_FIELDS) => void }) {
  const [open, setOpen] = useState(false)
  const [understood, setUnderstood] = useState(false)
  const [fields, setFields] = useState(EMPTY_FIELDS)
  const info = connectorInfo[id]
  const set = (key: keyof typeof EMPTY_FIELDS) => (e: React.ChangeEvent<HTMLInputElement>) => setFields({ ...fields, [key]: e.target.value })

  const missing =
    (id === 'odoo' && !(fields.url.trim() && fields.database.trim() && fields.apiKey.trim())) ||
    (id === 'netsuite' && !fields.account.trim())

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button className="w-full" onClick={() => setOpen(true)}>
        {retry ? 'Try again' : 'Connect'} <ArrowRight data-icon="inline-end" />
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Connect {info.name}</DialogTitle>
          <DialogDescription>FinSafe AI will request read-only access to invoices, bills, and contacts. We never modify your data.</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4 py-2">
          {id === 'odoo' && (
            <>
              <div className="flex flex-col gap-2">
                <Label htmlFor="odoo-url">Odoo URL</Label>
                <Input id="odoo-url" placeholder="https://yourcompany.odoo.com" value={fields.url} onChange={set('url')} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-2">
                  <Label htmlFor="odoo-db">Database</Label>
                  <Input id="odoo-db" value={fields.database} onChange={set('database')} />
                </div>
                <div className="flex flex-col gap-2">
                  <Label htmlFor="odoo-key">API key</Label>
                  <Input id="odoo-key" type="password" autoComplete="off" value={fields.apiKey} onChange={set('apiKey')} />
                </div>
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="odoo-login">Username (Odoo 18 or older only)</Label>
                <Input id="odoo-login" placeholder="you@company.com" value={fields.login} onChange={set('login')} />
              </div>
            </>
          )}
          {id === 'netsuite' && (
            <div className="flex flex-col gap-2">
              <Label htmlFor="account-id">Account ID</Label>
              <Input id="account-id" placeholder="1234567 or 1234567_SB1" value={fields.account} onChange={set('account')} />
            </div>
          )}
          {id === 'zoho' && <p className="text-sm text-muted-foreground">You'll be sent to Zoho to log in and approve read-only access, then brought back here with your data imported.</p>}
          <label className="flex items-center gap-3 rounded-xl bg-muted p-3 text-sm">
            <Checkbox checked={understood} onCheckedChange={(v) => setUnderstood(v === true)} />I understand
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            disabled={!understood || missing}
            onClick={() => {
              setOpen(false)
              onAuthorize(fields)
              setFields(EMPTY_FIELDS)
              setUnderstood(false)
            }}
          >
            Authorize
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function IntegrationCard({
  id, state, connecting, onConnect, onSync, onDisconnect,
}: {
  id: ConnectorId
  state?: ConnectorState
  connecting: boolean
  onConnect: (fields: typeof EMPTY_FIELDS) => void
  onSync: () => void
  onDisconnect: () => void
}) {
  const info = connectorInfo[id]
  const [confirm, setConfirm] = useState(false)
  const status: ConnectorStatus = connecting ? 'connecting' : (state?.status ?? 'disconnected')

  return (
    <Card className="flex flex-col border-border/70">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="flex size-12 items-center justify-center rounded-2xl bg-primary/8 font-bold text-primary">{info.initials}</span>
            <div>
              <CardTitle className="text-base">{info.name}</CardTitle>
              <CardDescription className="mt-1 text-xs">{info.auth}</CardDescription>
            </div>
          </div>
          <StatusBadge status={status} />
        </div>
        <p className="pt-2 text-sm text-muted-foreground">{info.description}</p>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-5">
        <ul className="flex flex-col gap-2 text-sm text-muted-foreground">
          {['Invoices & receivables', 'Bills & payables', 'Customers & vendors'].map((scope) => (
            <li key={scope} className="flex items-center gap-2">
              <Check className="size-4 text-emerald-600" />
              {scope}
            </li>
          ))}
        </ul>
        <div className="mt-auto">
          {status === 'connected' ? (
            <div className="flex flex-col gap-3">
              <div className="rounded-xl bg-emerald-50 p-3 text-xs text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
                <div className="flex items-center gap-2 font-semibold">
                  <CheckCircle2 className="size-4" /> Last synced: {timeAgo(state?.last_synced ?? null)}
                </div>
                <p className="mt-1 pl-6">{(state?.records_synced ?? 0).toLocaleString('en-IN')} records synced</p>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Button variant="outline" size="sm" onClick={onSync}>
                  <RefreshCw data-icon="inline-start" />Sync now
                </Button>
                <Button variant="ghost" size="sm" className="text-destructive" onClick={() => setConfirm(true)}>Disconnect</Button>
              </div>
            </div>
          ) : status === 'connecting' ? (
            <Button className="w-full" disabled>
              <RefreshCw className="animate-spin" data-icon="inline-start" />Connecting…
            </Button>
          ) : (
            <div className="flex flex-col gap-3">
              {status === 'error' && state?.error && (
                <div role="alert" className="flex gap-2 rounded-xl bg-destructive/10 p-3 text-xs text-destructive">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                  <span className="break-words">{state.error}</span>
                </div>
              )}
              <ConnectDialog id={id} retry={status === 'error'} onAuthorize={onConnect} />
            </div>
          )}
        </div>
      </CardContent>
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disconnect {info.name}?</AlertDialogTitle>
            <AlertDialogDescription>This will remove synced data from FinSafe AI. You can reconnect anytime.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirm(false)
                onDisconnect()
              }}
            >
              Disconnect
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  )
}

export function IntegrationsPage() {
  const [states, setStates] = useState<Partial<Record<ConnectorId, ConnectorState>>>({})
  const [ready, setReady] = useState(false)
  const [connecting, setConnecting] = useState<Set<ConnectorId>>(new Set())
  const [backendDown, setBackendDown] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const res = await getConnectors()
      setStates(Object.fromEntries(res.connectors.map((c) => [c.id, c])))
      setReady(res.ready_for_analysis)
      setBackendDown(false)
    } catch {
      setBackendDown(true)
    }
  }, [])

  const markConnecting = (id: ConnectorId, on: boolean) =>
    setConnecting((prev) => {
      const next = new Set(prev)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })

  useEffect(() => {
    refresh()
    // Returning from the Zoho consent screen: /integrations?connected=zoho or ?error=...
    const params = new URLSearchParams(window.location.search)
    if (params.get('connected')) toast.success(`${connectorInfo[params.get('connected') as ConnectorId]?.name ?? 'Platform'} connected`, { description: 'Read-only access is now active.' })
    if (params.get('error')) toast.error("Couldn't finish connecting", { description: params.get('error') ?? undefined })
    if (params.size) window.history.replaceState(null, '', '/integrations')
  }, [refresh])

  const handleConnect = async (id: ConnectorId, fields: typeof EMPTY_FIELDS) => {
    markConnecting(id, true)
    try {
      const result = await connectConnector(id, fields)
      if (result.redirect_url) {
        window.location.href = result.redirect_url
        return
      }
      toast.success(`${connectorInfo[id].name} connected`, { description: `${(result.records_synced ?? 0).toLocaleString('en-IN')} records synced, read-only.` })
      result.warnings?.forEach((w) => toast.warning(w))
    } catch (e) {
      toast.error(`Couldn't connect ${connectorInfo[id].name}`, { description: errorMessage(e) })
    }
    await refresh()
    markConnecting(id, false)
  }

  const handleSync = async (id: ConnectorId) => {
    const run = syncConnector(id)
    toast.promise(run, {
      loading: `Syncing ${connectorInfo[id].name}…`,
      success: (r) => `Synced ${(r.records_synced ?? 0).toLocaleString('en-IN')} records`,
      error: errorMessage,
    })
    await run.catch(() => undefined)
    await refresh()
  }

  const handleDisconnect = async (id: ConnectorId) => {
    try {
      await disconnectConnector(id)
      toast.success(`${connectorInfo[id].name} disconnected`, { description: 'Synced data was removed.' })
    } catch (e) {
      toast.error('Could not disconnect', { description: errorMessage(e) })
    }
    await refresh()
  }

  const liveConnected = CONNECTOR_IDS.filter((id) => states[id]?.status === 'connected').length
  const connected = liveConnected

  return (
    <>
      <main className="mx-auto max-w-6xl px-5 py-12 lg:px-8 lg:py-16">
        <div className="flex flex-col justify-between gap-8 md:flex-row md:items-end">
          <div>
            <Badge variant="outline" className="mb-4 border-primary/20 bg-primary/5 text-primary">Data connections</Badge>
            <h1 className="text-4xl font-semibold tracking-tight">Integrations</h1>
            <p className="mt-3 max-w-xl text-lg text-muted-foreground">
              Connect your accounting and ERP systems. FinSafe AI uses read-only access to analyze your financial data.
            </p>
          </div>
          <Card className="min-w-64 border-primary/15 bg-primary/5">
            <CardContent className="p-4">
              <div className="flex items-center justify-between text-sm font-semibold">
                <span>{Math.min(connected, 3)} of 3 connected</span>
                <span className="text-primary">{Math.min(connected, 3)}/3</span>
              </div>
              <Progress value={Math.min((connected / 3) * 100, 100)} className="mt-3" />
            </CardContent>
          </Card>
        </div>

        {backendDown && (
          <div role="alert" className="mt-8 flex items-center gap-3 rounded-2xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
            <AlertTriangle className="size-5 shrink-0" />
            <span>Can&apos;t reach the FinSafe backend. Start it with <code className="font-mono">uvicorn main:app --port 8000</code> in <code className="font-mono">finsafe/backend</code>.</span>
            <Button variant="outline" size="sm" className="ml-auto" onClick={refresh}>Retry</Button>
          </div>
        )}

        <div className="mt-10 flex flex-col justify-between gap-4 rounded-2xl border bg-card p-4 sm:flex-row sm:items-center">
          <div>
            <p className="font-semibold">Ready to see your risks?</p>
            <p className="text-sm text-muted-foreground">Analyze connected data and get your prioritized action plan.</p>
          </div>
          {ready ? (
            <Link href="/deep-analysis" className={buttonVariants({ size: 'lg' })}>
              Analyze my finances <ArrowRight data-icon="inline-end" />
            </Link>
          ) : (
            <Button size="lg" disabled title="Connect at least one platform">
              Analyze my finances <ArrowRight data-icon="inline-end" />
            </Button>
          )}
        </div>
        {!ready && <p className="mt-2 text-right text-xs text-muted-foreground">Connect at least one platform</p>}

        <div className="mt-10 grid gap-5 md:grid-cols-3">
          {CONNECTOR_IDS.map((id) => (
            <IntegrationCard
              key={id}
              id={id}
              state={states[id]}
              connecting={connecting.has(id)}
              onConnect={(fields) => handleConnect(id, fields)}
              onSync={() => handleSync(id)}
              onDisconnect={() => handleDisconnect(id)}
            />
          ))}
        </div>

        <section className="mt-20 grid gap-10 border-t pt-12 lg:grid-cols-[.9fr_1.1fr]">
          <div>
            <p className="text-sm font-semibold uppercase tracking-widest text-primary">Your data, your control</p>
            <h2 className="mt-3 text-2xl font-semibold tracking-tight">Security & permissions</h2>
            <ul className="mt-6 flex flex-col gap-4 text-sm text-muted-foreground">
              <li className="flex items-center gap-3"><ShieldCheck className="size-5 text-emerald-600" />Read-only scopes only</li>
              <li className="flex items-center gap-3"><KeyRound className="size-5 text-emerald-600" />Credentials encrypted at rest</li>
              <li className="flex items-center gap-3"><X className="size-5 text-emerald-600" />Disconnect and delete anytime</li>
            </ul>
          </div>
          <Accordion className="w-full">
            <AccordionItem value="safe">
              <AccordionTrigger>Is my data safe?</AccordionTrigger>
              <AccordionContent className="text-muted-foreground">
                We request read-only access, encrypt credentials, and show evidence behind every alert so your team stays in control.
              </AccordionContent>
            </AccordionItem>
            <AccordionItem value="change">
              <AccordionTrigger>Can FinSafe AI change my accounting data?</AccordionTrigger>
              <AccordionContent className="text-muted-foreground">
                No. Connections are read-only. FinSafe can draft recommended actions, but nothing is sent or changed without your approval.
              </AccordionContent>
            </AccordionItem>
            <AccordionItem value="read">
              <AccordionTrigger>Which data do you read?</AccordionTrigger>
              <AccordionContent className="text-muted-foreground">
                Invoices, bills, customers and vendors, and bank balances, which are what we need to calculate cash runway and risk signals.
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </section>
      </main>
    </>
  )
}
