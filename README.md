# FinSafe AI

An AI financial-risk copilot for small and mid-sized businesses. Connect your accounting or ERP system read-only, and FinSafe works out what is going wrong before it costs you: duplicate invoices, vendor price spikes, chronic late payers, cash shortfalls, and ten core business metrics.

**Every number is computed by deterministic code** (`backend/engines/`). Gemini only writes the explanations, email drafts and chat answers, and is told to use only the figures it is given. With no Gemini key the app falls back to plain templates built from the same numbers.

| Page | What it does |
|---|---|
| `/` | Product overview |
| `/integrations` | Connect Zoho Books, Oracle NetSuite or Odoo |
| `/deep-analysis` | Health score, cash runway, findings with evidence, collection-email drafter |
| `/what-if` | Four scenario tools: cash-runway simulator, early-payment discount cost, payment-terms cash release, customer-default stress test |
| `/insights` | Ten business metrics from whichever platforms are connected |

A floating chat button on every page opens the assistant in a right-hand panel.

## Architecture

```
frontend/   Next.js (App Router), TypeScript, Tailwind v4, shadcn/ui, Recharts
backend/    FastAPI
  connectors/   zoho.py  odoo.py  netsuite.py   -> unified schema (schema.py)
  engines/      cash, duplicates, vendor_spike, late_pay, score, insights   (pure Python)
  ai/gemini.py  explain findings, draft emails, grounded chat
  store.py      in-memory state (see Limitations)
docs/       original product spec and build plan
```

Every connector normalizes into the same invoices / bills / cash-balance schema, so the engines and insights never depend on which platform the data came from. Any one platform is enough, and several can be connected at once.

## Quick start

Requirements: Python 3.12+, Node 24+.

```bash
# 1. backend (single worker: state is in memory)
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # then fill in the values below
uvicorn main:app --port 8000

# 2. frontend (new terminal)
cd frontend
cp .env.example .env.local      # NEXT_PUBLIC_API_URL=http://localhost:8000
corepack pnpm install
corepack pnpm dev               # http://localhost:3000
```

Open http://localhost:3000/integrations and connect a platform.

## Configuration (`backend/.env`)

| Variable | Needed for | Notes |
|---|---|---|
| `FRONTEND_URL`, `CORS_ORIGINS` | all | Where the browser app runs |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | AI text (optional) | Without a key, template text is used |
| `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REDIRECT_URI`, `ZOHO_ACCOUNTS_URL` | Zoho Books | See below |
| `NETSUITE_CLIENT_ID`, `NETSUITE_CERTIFICATE_ID`, `NETSUITE_PRIVATE_KEY_PATH`, `NETSUITE_JWT_ALG` | NetSuite | OAuth 2.0 client-credentials (M2M) |

`.env` files are gitignored. Never commit real credentials.

### Zoho Books

One-time setup by whoever runs the app:

1. [Zoho API Console](https://api-console.zoho.in) (or `.com` / `.eu`) -> **Add Client** -> **Server-based Applications**.
2. Authorized Redirect URI: `<backend URL>/auth/zoho/callback`, for example `http://localhost:8000/auth/zoho/callback`.
3. Put the Client ID and Secret in `backend/.env`, with `ZOHO_REDIRECT_URI` set to that same URI and `ZOHO_ACCOUNTS_URL` set to your data centre's accounts host.

After that users enter nothing. **Connect** sends them to Zoho to log in and approve, then back with their data imported. Scopes are read-only (`*.READ`). The API data centre is taken from Zoho's token response.

### Odoo and NetSuite

- **Odoo**: URL, database and API key in the connect dialog. Odoo 19+ uses the JSON-2 API; older servers use `/jsonrpc` and also need the username.
- **NetSuite**: Account ID in the dialog; OAuth M2M credentials live in `backend/.env`. Data is read with SuiteQL.

Both are implemented from the vendors' documentation and covered by mocked-HTTP tests, but have **not been run against a live Odoo or NetSuite instance**. Expect to adjust field names (such as NetSuite status codes and date formats) against a real account.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/connectors` | Connector states |
| POST | `/api/connectors/{id}/connect` | Start/finish a connection (`zoho` returns a login URL) |
| POST | `/api/connectors/{id}/sync` | Re-pull data |
| POST | `/api/connectors/{id}/disconnect` | Remove the login and synced data |
| GET | `/auth/zoho/callback` | OAuth redirect target |
| GET | `/api/analysis?explain=0\|1` | Health score, runway, findings |
| GET | `/api/insights` | The ten business metrics |
| POST | `/api/whatif` | Collect invoices / delay bills and recompute the runway |
| GET | `/api/whatif/options` | Open invoices, bills and customers the tools can use |
| POST | `/api/whatif/early-discount` | Annualized cost and runway effect of an early-payment discount |
| POST | `/api/whatif/payment-terms` | Cash freed by faster collection / later vendor payment |
| POST | `/api/whatif/customer-default` | Runway impact if chosen customers never pay |
| POST | `/api/draft-email` | Draft a collection email (never sent) |
| POST | `/api/chat` | Question answered from the analysis only |

## Insights metrics

Days sales outstanding, days payables outstanding, on-time payment rate, average days to get paid, overdue receivables (aging), customer concentration, vendor concentration, monthly revenue, monthly spend, liquidity ratio. A metric that lacks the data it needs says what is missing instead of showing a made-up value.

## Tests and CI

```bash
cd backend && python -m pytest        # engines, insights, connectors (mocked HTTP)
cd frontend && corepack pnpm exec tsc --noEmit && corepack pnpm build
```

GitHub Actions (`.github/workflows/ci.yml`) runs both on every push and pull request.

## Limitations

- **State is in memory.** Connections and synced data live in the backend process and are lost on restart; reconnect to restore them. Run a single worker. Persistent, encrypted storage is not implemented yet.
- **Single tenant.** There are no user accounts; everyone using one backend instance shares its connections.
- **Gemini sees your data.** For explanations it receives computed findings and summary figures; for chat it receives the full analysis (including document IDs and customer/vendor names).
- **Zoho organization.** The first organization on the account is used; there is no picker yet.
- **Cash.** A balance is read from Zoho bank and cash accounts. If none is found, cash-based figures (runway, liquidity, what-if) are shown as unavailable rather than as zero.
- FinSafe AI provides decision support, not financial or legal advice.

## Docs

- [Product and UI spec](docs/ui-product-spec.txt)
- [Original build plan](docs/build-plan.txt)

## License

No license has been chosen yet. Add a `LICENSE` file before making the repository public.
