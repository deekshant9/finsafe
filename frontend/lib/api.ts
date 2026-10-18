// Typed client for the FinSafe backend. Base URL: NEXT_PUBLIC_API_URL.

const BASE = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')

export type ConnectorId = 'zoho' | 'netsuite' | 'odoo'
export type ConnectorStatus = 'disconnected' | 'connecting' | 'connected' | 'error'

export interface ConnectorState {
  id: ConnectorId
  name: string
  auth: string
  status: ConnectorStatus
  records_synced: number
  last_synced: string | null
  error: string | null
}

export interface ConnectorsResponse {
  connectors: ConnectorState[]
  ready_for_analysis: boolean
}

export interface ConnectResult {
  id: ConnectorId
  status: ConnectorStatus
  records_synced?: number
  last_synced?: string
  warnings?: string[]
  /** Set when the browser must continue at the provider's consent screen (Zoho). */
  redirect_url?: string
}

export interface ConnectPayload {
  url?: string
  database?: string
  apiKey?: string
  login?: string
  account?: string
}

export interface Finding {
  id: string
  type: string
  severity: 'high' | 'medium' | 'low'
  title: string
  amount_at_risk: number
  evidence: string[]
  detail: Record<string, unknown>
  explanation: string | null
  action: string | null
}

export interface RunwayPoint {
  day: number
  date: string
  balance: number
}

export interface RunwayDriver {
  kind: 'invoice' | 'bill'
  id: string
  party: string
  amount: number
  date: string
  day: number
}

export interface Runway {
  starting_balance: number
  daily_operating_spend: number
  runway_day: number | null
  horizon_days: number
  /** False when no bank balance was found: cash is unknown, so there is no balance curve or runway. */
  cash_known: boolean
  balance_30: number | null
  balance_60: number | null
  balance_90: number | null
  min_balance: number | null
  series: RunwayPoint[]
  drivers: RunwayDriver[]
}

export interface Analysis {
  as_of: string
  company: string | null
  currency: string
  health: { score: number; label: string; deductions: { points: number; reason: string }[]; overdue_ratio: number }
  runway: Runway
  findings: Finding[]
  late_payment_ranking: { customer: string; risk: number; avg_days_late: number; outstanding: number; open_invoice_ids: string[] }[]
  summary: {
    cash_balance: number
    open_receivables: number
    overdue_receivables: number
    open_payables: number
    invoice_count: number
    bill_count: number
    total_amount_at_risk: number
  }
  warnings: string[]
  ai_enabled: boolean
  ai_generated?: boolean
  explained: boolean
}

export interface WhatIfResult {
  before: Runway
  after: Runway
  runway_before: number | null
  runway_after: number | null
  runway_gain_days: number | null
  no_longer_runs_out: boolean
  cash_collected: number
  payments_delayed: number
  lowest_balance_change: number
}

export interface EmailDraft {
  subject: string
  body: string
  ai_generated: boolean
  sent: false
  invoice: { id: string; customer: string; outstanding: number; due_date: string; days_overdue: number }
}

export type MetricUnit = 'days' | 'percent' | 'ratio' | 'currency'

export interface Metric {
  id: string
  title: string
  unit: MetricUnit
  available: boolean
  value: number | null
  change_pct: number | null
  description: string
  formula: string
  note: string | null
  /** Why a metric can't be computed yet (only when available is false). */
  reason: string | null
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  detail: Record<string, any>
}

export interface Insights {
  as_of: string
  company: string | null
  currency: string
  sources: ConnectorId[]
  metrics: Metric[]
  available_count: number
}

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch {
    throw new ApiError(`Can't reach the FinSafe backend at ${BASE}. Is it running?`, 0)
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = typeof body?.detail === 'string' ? body.detail : res.statusText
    throw new ApiError(detail || `Request failed (${res.status})`, res.status)
  }
  return res.json() as Promise<T>
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

// ── connectors ──
export const getConnectors = () => request<ConnectorsResponse>('/api/connectors')
export const connectConnector = (id: ConnectorId, payload?: ConnectPayload) =>
  post<ConnectResult>(`/api/connectors/${id}/connect`, payload ?? {})
export const syncConnector = (id: ConnectorId) => post<ConnectResult>(`/api/connectors/${id}/sync`)
export const disconnectConnector = (id: ConnectorId) =>
  post<{ id: ConnectorId; status: 'disconnected' }>(`/api/connectors/${id}/disconnect`)

// ── analysis ──
export const getAnalysis = (explain = true) => request<Analysis>(`/api/analysis?explain=${explain ? 1 : 0}`)
export const runWhatIf = (body: { collect_invoice_ids: string[]; delay_bill_ids: string[]; delay_days: number }) =>
  post<WhatIfResult>('/api/whatif', body)
export const draftEmail = (invoice_id: string) => post<EmailDraft>('/api/draft-email', { invoice_id })
export const askChat = (question: string) => post<{ answer: string; ai_generated: boolean }>('/api/chat', { question })

// ── insights ──
export const getInsights = () => request<Insights>('/api/insights')

// ── what-if tools ──
export interface RunwayEffect {
  runway_before: number | null
  runway_after: number | null
  runway_gain_days: number | null
  no_longer_runs_out: boolean
  newly_runs_out: boolean
  lowest_balance_before: number | null
  lowest_balance_after: number | null
  before_series: RunwayPoint[]
  after_series: RunwayPoint[]
}

export interface WhatIfOptions {
  currency: string
  cash_known: boolean
  invoices: { id: string; customer: string; outstanding: number; due_date: string; days_overdue: number }[]
  bills: { id: string; vendor: string; outstanding: number; due_date: string; days_overdue: number }[]
  customers: { name: string; outstanding: number; invoice_count: number }[]
  truncated: boolean
}

export interface EarlyDiscountResult {
  invoices: {
    id: string
    customer: string
    outstanding: number
    discount: number
    cash_received: number
    expected_day_without_offer: number
    days_accelerated: number
    annualized_cost_pct: number | null
    pays_sooner_anyway: boolean
  }[]
  total_discount_cost: number
  discount_given_for_nothing: number
  cash_pulled_forward: number
  avg_days_accelerated: number | null
  annualized_cost_pct: number | null
  runway: RunwayEffect | null
}

export interface PaymentTermsResult {
  basis_days: number
  daily_invoiced: number
  daily_billed: number
  cash_freed_from_receivables: number | null
  cash_freed_from_payables: number | null
  total_cash_freed: number
  dso: { before: number; after: number } | null
  dpo: { before: number; after: number } | null
  notes: string[]
  runway: RunwayEffect | null
}

export interface CustomerDefaultResult {
  customers: { name: string; outstanding: number; invoices: number; overdue: number }[]
  receivables_lost: number
  share_of_open_receivables_pct: number
  open_receivables_after: number
  runway: RunwayEffect | null
}

export const getWhatIfOptions = () => request<WhatIfOptions>('/api/whatif/options')
export const runEarlyDiscount = (body: { invoice_ids: string[]; discount_pct: number; pay_within_days: number }) =>
  post<EarlyDiscountResult>('/api/whatif/early-discount', body)
export const runPaymentTerms = (body: { collect_days_faster: number; pay_days_later: number }) =>
  post<PaymentTermsResult>('/api/whatif/payment-terms', body)
export const runCustomerDefault = (body: { customers: string[] }) =>
  post<CustomerDefaultResult>('/api/whatif/customer-default', body)
