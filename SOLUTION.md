# Zycus TAM Agent: Agentic Ticket Triage Solution

## 1. Design & Architecture
The Zycus TAM Triage Agent evaluates incoming support tickets through an autonomous investigation loop powered by Google Gemini (`gemini-3.5-flash-lite`) and the `google-genai` SDK using native tool calling. Rather than applying a fixed prompt sequence, the agent dynamically selects investigation tools based on initial symptoms and accumulated observations.

```
Incoming Ticket ---> [Agent Loop (Gemini)] <---> [ToolKit: In-Memory Indexed Corpus]
                           |                      - lookup_account (domain/ID/name)
                           |                      - get_account_health (renewal/signals)
                           |                      - get_usage_trend (3mo vs 3mo user drop)
                           |                      - find_related_tickets (dupes/history)
                           |                      - search_kb (lexical + module/error boost)
                           |                      - read_kb_article (body & superseding links)
                           |                      - get_support_policy (KB-2914/5930/6401)
                           v
           [Schema Validation & Grounding] ---> JSON Output + Reasoning Trace
```

- **Dynamic Tool Calling**: 7 specialized tools expose account context, usage trends, historical tickets, and KB articles. The agent inspects observations step-by-step and initiates follow-up lookups when ambiguity arises.
- **Stopping Conditions**: The loop terminates when the agent gathers sufficient evidence to classify category, priority, routing, escalation, and duplicate status, or when bounded iterations are reached.
- **Untrusted Input Defense**: Ticket subject and body are treated as untrusted data; priority and routing strictly adhere to verified corpus policies (`KB-2914`, `KB-5930`, `KB-6401`).
- **Grounding & Schema Enforcement**: Output format strictly complies with `tam_agent/schema.py`. The agent enforces that `account_id` is resolved via domain matching, citations match verified KB IDs, and `duplicate_of` cannot be the ticket itself.

---

## 2. Evaluation & Production Quality Metrics
**Review Benchmark**: 10/10 review tickets pass automated validation (`scripts/check_outputs.py`).

| Ticket | Category | Priority | Route | Escalate | Notes / Grounded Policy |
|---|---|---|---|:---:|---|
| TKT-144939 | incident | P1 | L2_engineering | ✅ | High-severity OCR outage (`KB-3985`, `KB-2914`) |
| TKT-119877 | how_to | P3 | L1_support | ✅ | Risk override: 61d renewal, 38% user drop (`KB-2914`) |
| TKT-160477 | incident | P3 | L1_support | ❌ | Standard single-user invoice issue |
| TKT-192732 | duplicate | P3 | L1_support | ❌ | Linked to prior open ticket within 7 days (`KB-5930`) |
| TKT-169327 | incident | P3 | L2_engineering | ❌ | Account resolved via email domain match |
| TKT-160067 | feature_request | P4 | product | ❌ | Forwarded to product team for roadmap review |
| TKT-160094 | incident | P2 | L2_engineering | ❌ | High-impact ERP integration sync failure |
| TKT-128253 | needs_info | P3 | L1_support | ❌ | Ambiguous description (`needs_clarification: true`) |
| TKT-135081 | security | P1 | security | ✅ | Cross-tenant data exposure protocol (`KB-6401`) |
| TKT-158764 | how_to | P4 | L1_support | ❌ | Routine module navigation query |

**Measurable Quality Criteria for Production Readiness**:
1. **Schema Compliance & Type Safety**: 100% adherence to `schema.py` (valid enums, non-empty traces, bounded confidence).
2. **Account Resolution Accuracy**: >99% precision/recall resolving missing `account_id` via domain and company name maps.
3. **Classification & Routing Macro-F1**: Measure Category, Priority, and Routing against expert-labeled test sets ($\ge 0.90$ target; 100% recall on P1 security leaks).
4. **Citation Faithfulness**: Zero hallucinated KB IDs; all cited articles exist in the corpus and are verified current (not deprecated).
5. **Duplicate Detection F1**: High precision on 7-day duplicate matching per `KB-5930` with zero self-referencing duplicates.
6. **TAM Alerting Precision**: High recall on alerting TAMs for accounts within 90 days of renewal with >25% usage drops or $\ge 2$ escalations.
7. **Customer Reply Safety & Tone**: LLM-as-a-judge / human audit ensuring helpful, empathetic replies without liability-admitting text.

---

## 3. Scaling to 10x (30,000 Tickets & 4,000 Articles)
- **Database & Indexing**: Replace in-memory dictionaries with indexed PostgreSQL for tickets/accounts. Use SQLite FTS5 or hybrid dense/sparse vector search (e.g., Qdrant/pgvector + BM25) for KB retrieval.
- **Latency & Cost**: Cache frequent policy articles (`KB-2914`, `KB-6401`) and account metadata in Redis to eliminate redundant lookups.
- **Throughput**: Decouple triage ingestion with distributed worker queues (Celery/RabbitMQ) and token-bucket rate limiting.

---

## 4. Limitations
- Pure lexical token matching in KB search may miss colloquial synonyms not present in titles or error codes.
- Multi-issue tickets with competing symptoms are triaged strictly under their highest-severity symptom.
- Free-tier API rate limits can trigger HTTP 429 errors during batch execution; `agent.py` dynamically extracts server-suggested `retryDelay` to back off accurately, and `run.py` includes a configurable `--delay` flag. Production requires paid tier concurrency.

---

## 5. How to Run
```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1  |  macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # Configure GEMINI_API_KEY
python scripts/validate_data.py
python run.py --set review
python scripts/check_outputs.py
```
