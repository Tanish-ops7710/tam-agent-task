"""Lookup and retrieval tools with indexes built once in Context.

All date calculations use AS_OF_DATE = '2026-10-20'.
"""
from datetime import datetime, date
import re
from typing import Any

AS_OF_DATE = "2026-10-20"
AS_OF = datetime.strptime(AS_OF_DATE, "%Y-%m-%d").date()


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_\-]+", (text or "").lower()))


class ToolKit:
    def __init__(self, accounts_df, tickets_df, usage_df, kb_list):
        self.accounts_df = accounts_df
        self.tickets_df = tickets_df
        self.usage_df = usage_df
        self.kb_list = kb_list

        self._build_indexes()

    def _build_indexes(self):
        # 1. Accounts
        self.account_by_id = {}
        self.account_by_domain = {}
        self.account_by_name = {}

        for _, row in self.accounts_df.iterrows():
            d = row.to_dict()
            aid = str(d.get("account_id", "")).strip()
            dom = str(d.get("domain", "")).strip().lower()
            name = str(d.get("company_name", "")).strip().lower()
            if aid:
                self.account_by_id[aid] = d
            if dom:
                self.account_by_domain[dom] = d
            if name:
                self.account_by_name[name] = d

        # 2. Tickets
        self.tickets_by_id = {}
        self.tickets_by_account = {}
        for _, row in self.tickets_df.iterrows():
            t = row.to_dict()
            tid = str(t.get("ticket_id", "")).strip()
            aid = str(t.get("account_id", "")).strip()
            if not aid and t.get("requester_domain"):
                dom = str(t["requester_domain"]).strip().lower()
                if dom in self.account_by_domain:
                    aid = self.account_by_domain[dom]["account_id"]
                    t["account_id"] = aid

            if tid:
                self.tickets_by_id[tid] = t
            if aid:
                self.tickets_by_account.setdefault(aid, []).append(t)

        # 3. Usage metrics by account
        self.usage_by_account = {}
        for _, row in self.usage_df.iterrows():
            u = row.to_dict()
            aid = str(u.get("account_id", "")).strip()
            if aid:
                self.usage_by_account.setdefault(aid, []).append(u)
        # Sort each account's usage by month ascending
        for aid, rows in self.usage_by_account.items():
            rows.sort(key=lambda x: str(x.get("month", "")))

        # 4. Knowledge Base
        self.kb_by_id = {}
        self.policy_articles = []
        self.kb_tokens = {}  # id -> token set

        for art in self.kb_list:
            aid = art["id"]
            meta = art.get("meta", {})
            self.kb_by_id[aid] = art
            if meta.get("type") == "policy":
                self.policy_articles.append(art)

            title = str(meta.get("title", ""))
            module = str(meta.get("module", ""))
            error_codes = " ".join(meta.get("error_codes", []))
            body = str(art.get("body", ""))
            searchable = f"{aid} {title} {module} {error_codes} {body}"
            self.kb_tokens[aid] = _tokenize(searchable)

    # ------------------ Tool Implementations ------------------

    def lookup_account(self, account_id: str | None = None, requester_domain: str | None = None,
                       company_name: str | None = None) -> dict:
        """Resolve account details by ID, requester_domain, or company_name."""
        acc = None
        match_type = None

        if account_id and account_id.strip() in self.account_by_id:
            acc = self.account_by_id[account_id.strip()]
            match_type = "account_id"
        elif requester_domain and requester_domain.strip().lower() in self.account_by_domain:
            acc = self.account_by_domain[requester_domain.strip().lower()]
            match_type = "requester_domain"
        elif company_name:
            cn = company_name.strip().lower()
            if cn in self.account_by_name:
                acc = self.account_by_name[cn]
                match_type = "company_name_exact"
            else:
                for k, v in self.account_by_name.items():
                    if cn in k or k in cn:
                        acc = v
                        match_type = "company_name_partial"
                        break

        if not acc:
            return {"found": False, "message": "Account could not be resolved"}

        return {
            "found": True,
            "match_by": match_type,
            "account_id": acc.get("account_id"),
            "company_name": acc.get("company_name"),
            "domain": acc.get("domain"),
            "tier": acc.get("tier"),
            "arr_band": acc.get("arr_band"),
            "modules_licensed": acc.get("modules_licensed", "").split(";"),
            "open_escalations": int(acc.get("open_escalations", 0) or 0),
            "renewal_date": acc.get("renewal_date"),
            "nps_last": acc.get("nps_last") or None,
            "sso_enabled_date": acc.get("sso_enabled_date") or None,
        }

    def get_account_health(self, account_id: str) -> dict:
        """Inspect contract tier, renewal window from 2026-10-20, escalations, NPS, and TAM signals."""
        acc = self.account_by_id.get(account_id.strip() if account_id else "")
        if not acc:
            return {"error": f"Account '{account_id}' not found"}

        renewal_str = acc.get("renewal_date")
        days_to_renewal = None
        renewal_within_90d = False
        if renewal_str:
            try:
                r_date = datetime.strptime(renewal_str, "%Y-%m-%d").date()
                days_to_renewal = (r_date - AS_OF).days
                renewal_within_90d = 0 <= days_to_renewal <= 90
            except Exception:
                pass

        open_esc = int(acc.get("open_escalations", 0) or 0)
        tier = acc.get("tier")

        signals = []
        if renewal_within_90d:
            signals.append(f"Account within renewal window: {days_to_renewal} days to renewal ({renewal_str})")
        if open_esc >= 2:
            signals.append(f"High executive escalation risk: {open_esc} open escalations")
        elif open_esc > 0:
            signals.append(f"{open_esc} open escalation on account")
        if tier:
            signals.append(f"{tier} tier contract")

        return {
            "account_id": acc.get("account_id"),
            "company_name": acc.get("company_name"),
            "tier": tier,
            "renewal_date": renewal_str,
            "days_to_renewal": days_to_renewal,
            "renewal_within_90d": renewal_within_90d,
            "open_escalations": open_esc,
            "nps_last": acc.get("nps_last"),
            "key_signals": signals,
        }

    def get_usage_trend(self, account_id: str) -> dict:
        """Calculate recent 3 months vs prior 3 months active user trend relative to AS_OF_DATE (2026-10-20)."""
        rows = self.usage_by_account.get(account_id.strip() if account_id else "", [])
        if not rows:
            return {"account_id": account_id, "usage_records": 0, "active_users_decline_pct": 0, "decline_gt_25": False}

        if len(rows) >= 6:
            recent_3 = rows[-3:]
            prior_3 = rows[-6:-3]
        elif len(rows) >= 2:
            mid = len(rows) // 2
            prior_3 = rows[:mid]
            recent_3 = rows[mid:]
        else:
            recent_3 = rows
            prior_3 = []

        def avg_users(sample):
            vals = [float(x.get("active_users", 0) or 0) for x in sample]
            return sum(vals) / len(vals) if vals else 0.0

        rec_avg = avg_users(recent_3)
        prior_avg = avg_users(prior_3)

        decline_pct = 0.0
        if prior_avg > 0:
            diff = prior_avg - rec_avg
            if diff > 0:
                decline_pct = round((diff / prior_avg) * 100, 1)

        decline_gt_25 = decline_pct > 25.0

        return {
            "account_id": account_id,
            "records_count": len(rows),
            "recent_3mo_avg_active_users": round(rec_avg, 1),
            "prior_3mo_avg_active_users": round(prior_avg, 1),
            "active_users_decline_pct": decline_pct,
            "decline_gt_25": decline_gt_25,
            "recent_months": [f"{r.get('month')}: {r.get('active_users')} users, {r.get('documents_processed')} docs" for r in recent_3],
        }

    def find_related_tickets(self, account_id: str, query: str | None = None,
                             error_code: str | None = None, current_ticket_id: str | None = None, limit: int = 5) -> list[dict]:
        """Find related or duplicate tickets for an account by error_code or symptom similarity."""
        tix = self.tickets_by_account.get(account_id.strip() if account_id else "", [])
        if not tix:
            return []

        q_tokens = _tokenize(query) if query else set()
        clean_code = (error_code or "").strip().upper()

        scored = []
        for t in tix:
            t_id = t.get("ticket_id")
            if current_ticket_id and t_id == current_ticket_id:
                continue

            score = 0
            t_code = (t.get("error_code") or "").strip().upper()
            if clean_code and t_code and clean_code == t_code:
                score += 50
            elif clean_code and clean_code in (str(t.get("description", "")) + str(t.get("subject", ""))).upper():
                score += 40

            if q_tokens:
                t_tokens = _tokenize(f"{t.get('subject', '')} {t.get('description', '')} {t.get('module', '')}")
                common = q_tokens.intersection(t_tokens)
                score += len(common) * 2

            if score > 0:
                created_str = t.get("created_at", "")
                days_ago = None
                if created_str:
                    try:
                        t_date = datetime.fromisoformat(created_str.replace("Z", "")).date()
                        days_ago = (AS_OF - t_date).days
                    except Exception:
                        pass

                scored.append({
                    "ticket_id": t_id,
                    "created_at": created_str,
                    "days_ago_from_as_of": days_ago,
                    "status": t.get("status"),
                    "module": t.get("module"),
                    "error_code": t.get("error_code") or None,
                    "subject": t.get("subject"),
                    "score": score,
                    "is_recent_open_duplicate_candidate": (
                        days_ago is not None and 0 <= days_ago <= 7 and t.get("status") in ("open", "pending_customer")
                    ),
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def search_kb(self, query: str, module: str | None = None, error_code: str | None = None,
                  doc_type: str | None = None, limit: int = 5) -> list[dict]:
        """Search knowledge base with lexical matching, module & error_code weighting, and deprecation down-ranking."""
        q_tokens = _tokenize(query)
        clean_code = (error_code or "").strip().upper()
        clean_mod = (module or "").strip().lower()

        results = []
        for aid, art in self.kb_by_id.items():
            meta = art.get("meta", {})
            status = meta.get("status", "current")
            art_type = meta.get("type", "")
            art_codes = [str(c).upper() for c in meta.get("error_codes", [])]
            art_mod = str(meta.get("module", "")).lower()

            score = 0
            if clean_code and clean_code in art_codes:
                score += 60
            elif clean_code and clean_code in self.kb_tokens[aid]:
                score += 30

            if clean_mod and (clean_mod == art_mod or art_mod == "all"):
                score += 15

            if doc_type and doc_type.lower() == art_type.lower():
                score += 10

            overlap = q_tokens.intersection(self.kb_tokens[aid])
            score += len(overlap) * 2

            if status == "deprecated":
                score -= 20

            if score > 0:
                body_snippet = art.get("body", "").strip()[:180].replace("\n", " ") + "..."
                results.append({
                    "id": aid,
                    "title": meta.get("title"),
                    "module": meta.get("module"),
                    "type": art_type,
                    "status": status,
                    "superseded_by": meta.get("superseded_by"),
                    "related": meta.get("related", []),
                    "score": score,
                    "snippet": body_snippet,
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def read_kb_article(self, article_id: str) -> dict:
        """Fetch full content and metadata for a specific KB article, resolving superseded_by link if deprecated."""
        art = self.kb_by_id.get(article_id.strip() if article_id else "")
        if not art:
            return {"error": f"Article '{article_id}' not found"}

        meta = art.get("meta", {})
        res = {
            "id": art["id"],
            "title": meta.get("title"),
            "module": meta.get("module"),
            "type": meta.get("type"),
            "status": meta.get("status"),
            "error_codes": meta.get("error_codes", []),
            "superseded_by": meta.get("superseded_by"),
            "related": meta.get("related", []),
            "body": art.get("body", "").strip(),
        }

        if meta.get("status") == "deprecated" and meta.get("superseded_by"):
            sup_id = meta.get("superseded_by")
            sup_art = self.kb_by_id.get(sup_id)
            if sup_art:
                res["superseding_article"] = {
                    "id": sup_id,
                    "title": sup_art.get("meta", {}).get("title"),
                    "snippet": sup_art.get("body", "").strip()[:200].replace("\n", " "),
                }

        return res

    def get_support_policy(self, topic: str | None = None) -> list[dict]:
        """Return authoritative support policies (e.g. priority & escalation, duplicate handling, data exposure)."""
        clean_topic = (topic or "").strip().lower()
        policies = []
        for p in self.policy_articles:
            pid = p["id"]
            title = p.get("meta", {}).get("title", "")
            body = p.get("body", "").strip()
            if not clean_topic or clean_topic in title.lower() or clean_topic in body.lower():
                policies.append({
                    "id": pid,
                    "title": title,
                    "summary": body[:300].replace("\n", " ") + "...",
                    "full_text": body,
                })
        return policies
