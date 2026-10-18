"""Gemini layer: explains findings, drafts emails, answers questions.

Hard rule: every number comes from the engines. Gemini is only given computed values and
told to use ONLY those. When no API key is set (or a call fails) each function falls back
to a deterministic template built from the same numbers, and says so via `ai_generated`.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import config

log = logging.getLogger(__name__)

_client = None


def enabled() -> bool:
    return bool(config.GEMINI_API_KEY)


def _generate(system: str, prompt: str, as_json: bool) -> str:
    global _client
    from google import genai
    from google.genai import types

    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    cfg = types.GenerateContentConfig(
        system_instruction=system, temperature=0.2,
        response_mime_type="application/json" if as_json else "text/plain")
    return _client.models.generate_content(model=config.GEMINI_MODEL, contents=prompt, config=cfg).text or ""


def money(amount: float, currency: str = "") -> str:
    """INR gets Indian digit grouping (12,34,567); other currencies use thousands separators."""
    n = int(round(abs(amount)))
    sign = "-" if amount < 0 else ""
    if currency == "INR":
        s = str(n)
        if len(s) > 3:
            head, tail = s[:-3], s[-3:]
            groups = []
            while len(head) > 2:
                groups.insert(0, head[-2:])
                head = head[:-2]
            if head:
                groups.insert(0, head)
            s = ",".join(groups + [tail])
        return f"{sign}₹{s}"
    return f"{sign}{currency} {n:,}".replace("  ", " ").strip()


# ── explain ──
EXPLAIN_SYSTEM = (
    "You are a financial risk analyst. Use ONLY the numbers provided. Never compute, estimate or invent "
    "figures. Explain each finding in 2 sentences and give one concrete action. Return JSON: "
    '{"items":[{"id":"<finding id>","explanation":"...","action":"..."}]}.')


def explain(findings: list[dict], summary: dict, currency: str) -> tuple[dict[str, dict], bool]:
    """Returns ({finding_id: {explanation, action}}, ai_generated)."""
    fallback = {f["id"]: _template(f, currency) for f in findings}
    if not findings or not enabled():
        return fallback, False
    try:
        payload = {"currency": currency, "summary": summary,
                   "findings": [{k: f[k] for k in ("id", "type", "severity", "title", "amount_at_risk", "evidence", "detail")} for f in findings]}
        raw = _generate(EXPLAIN_SYSTEM, json.dumps(payload, default=str), as_json=True)
        items = {i["id"]: {"explanation": i["explanation"], "action": i["action"]}
                 for i in json.loads(raw).get("items", []) if {"id", "explanation", "action"} <= i.keys()}
        return {**fallback, **{k: v for k, v in items.items() if k in fallback}}, bool(items)
    except Exception as e:  # network, quota, bad JSON ... never break the dashboard over prose
        log.warning("Gemini explain failed, using templates: %s", e)
        return fallback, False


def _template(f: dict, cur: str) -> dict:
    d, m, ev = f["detail"], lambda x: money(x, cur), ", ".join(f["evidence"][:3])
    t = f["type"]
    if t.startswith("duplicate_"):
        kind = t.split("_")[1]
        return {"explanation": f"{ev} look like the same {kind} for {d['party']}: {m(d['amounts'][0])} entered twice, {d['days_apart']} days apart. "
                               f"If both are real, {m(f['amount_at_risk'])} is being double-counted or double-paid.",
                "action": f"Check {ev} against the source documents and void or credit the duplicate."}
    if t == "vendor_price_spike":
        return {"explanation": f"{d['vendor']}'s latest bill is {m(d['latest_amount'])} against a usual {m(d['median_previous'])} ({d['increase_pct']}% higher). "
                               f"That is {m(f['amount_at_risk'])} above what earlier bills suggest.",
                "action": f"Ask {d['vendor']} to justify the increase before paying bill {f['evidence'][0]}."}
    if t == "late_payer":
        return {"explanation": f"{d['customer']} has paid about {d['avg_days_late']} days late on average, and {d['late_ratio']:.0%} of their invoices were late. "
                               f"They currently owe {m(d['outstanding'])}.",
                "action": f"Send {d['customer']} a payment reminder for {ev} now, and consider shorter terms or a deposit."}
    if t == "cash_shortfall":
        out = d.get("largest_outflow")
        big = f" The largest outflow is {m(abs(out['amount']))} to {out['party']} on {out['date']}." if out else ""
        return {"explanation": f"Projected cash falls below zero in {d['runway_day']} days, bottoming out at {m(d['lowest_balance'])}.{big}",
                "action": "Collect overdue invoices first, then negotiate or delay the largest upcoming bills; try the what-if simulator."}
    return {"explanation": f["title"], "action": "Review the evidence listed for this finding."}


# ── email ──
EMAIL_SYSTEM = (
    "You write polite but firm payment-reminder emails for a finance team. Use ONLY the facts provided; never invent "
    "amounts, dates or invoice numbers. Keep it under 150 words. Return JSON: {\"subject\":\"...\",\"body\":\"...\"}.")


def draft_email(invoice: dict, stats: Optional[dict], company: str, currency: str) -> dict:
    facts = {"company": company, "currency": currency, "invoice": invoice, "customer_payment_history": stats}
    if enabled():
        try:
            out = json.loads(_generate(EMAIL_SYSTEM, json.dumps(facts, default=str), as_json=True))
            if out.get("subject") and out.get("body"):
                return {**out, "ai_generated": True}
        except Exception as e:
            log.warning("Gemini draft_email failed, using template: %s", e)
    overdue = invoice["days_overdue"]
    timing = f"was due on {invoice['due_date']} ({overdue} days ago)" if overdue > 0 else f"is due on {invoice['due_date']}"
    return {
        "subject": f"Payment reminder: invoice {invoice['id']}",
        "body": (f"Dear {invoice['customer']} team,\n\nI hope you're well. This is a reminder that invoice {invoice['id']} for "
                 f"{money(invoice['outstanding'], currency)} {timing}.\n\nCould you confirm when we can expect payment? "
                 f"If it has already been sent, please share the payment reference so we can reconcile it.\n\n"
                 f"Thank you for your prompt attention.\n\nKind regards,\n{company}"),
        "ai_generated": False}


# ── chat ──
CHAT_SYSTEM = (
    "You are FinSafe AI, a financial risk copilot. Answer ONLY from the analysis JSON provided. If the answer isn't "
    "in it, say you don't have that data. Never calculate new figures or invent numbers; quote them as given. "
    "Be concise (under 120 words). You provide decision support, not financial or legal advice.")


def chat(question: str, analysis: dict) -> dict:
    if enabled():
        try:
            prompt = f"Analysis JSON:\n{json.dumps(analysis, default=str)}\n\nQuestion: {question}"
            return {"answer": _generate(CHAT_SYSTEM, prompt, as_json=False).strip(), "ai_generated": True}
        except Exception as e:
            log.warning("Gemini chat failed, using summary: %s", e)
    cur = analysis.get("currency", "")
    lines = [f"Health score {analysis['health']['score']}/100 ({analysis['health']['label']})."]
    rw = analysis["runway"]["runway_day"]
    lines.append(f"Cash runs out in {rw} days." if rw is not None else "Cash stays positive for the next 90 days.")
    lines += [f"- {f['title']} ({money(f['amount_at_risk'], cur)} at risk)" for f in analysis["findings"][:4]]
    return {"answer": "Gemini isn't configured, so here is the computed summary instead:\n" + "\n".join(lines),
            "ai_generated": False}
