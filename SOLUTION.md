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

## 2. Evaluation Strategy
1. **Schema Compliance**: 10/10 review tickets pass automated structural and type validation (`scripts/check_outputs.py`).
2. **Account Resolution Accuracy**: Correctly maps 100% of missing `account_id` fields via unique `requester_domain` (e.g. `TKT-169327` resolved to `ACC-4850`).
3. **Policy-Based Routing & Escalation**:
   - **Data Exposure / Security**: Classifies suspected data leaks as `security`/`P1` routed to `security` (`TKT-135081` per `KB-6401`).
   - **Account Risk Override**: Elevates priority to `P3` and notifies TAM when within 90-day renewal and active users drop >25% (`TKT-119877` per `KB-2914`).
   - **Ambiguous Tickets**: Appropriately triggers `needs_clarification=True` without guessing (`TKT-128253`).
4. **Duplicate Detection Precision**: Flags duplicates created within 7 days on the same module/error while ignoring resolved history (`TKT-192732` linked to `TKT-122022`).
5. **Trace Faithfulness**: Reasoning traces record real, chronological tool actions and bounded observations without hallucinated data.

---

## 3. Scaling to 10x (30,000 Tickets & 4,000 Articles)
- **Database & Indexing**: Replace in-memory dictionaries with indexed SQLite/PostgreSQL for account and ticket history. Use SQLite FTS5 or BM25/hybrid vector search with metadata filtering for KB retrieval.
- **Latency & Cost**: Cache policy articles and frequent account lookups in Redis to eliminate redundant tool executions. Batch pre-triage embeddings offline.
- **Rate-Limiting & Throughput**: Implement exponential backoff queues with worker pools to respect model rate limits across multi-tenant workloads.

---

## 4. Limitations
- Pure lexical search in KB lookup may miss colloquial synonyms not present in error codes or titles.
- Complex multi-issue tickets with competing symptoms are currently triaged under the single highest-severity category.

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
