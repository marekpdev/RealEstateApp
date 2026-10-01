# 💰 Cost Optimization

An AI system's bill is set by three things: how many **model calls** each request makes, how many **tokens** each call carries, and how many **paid vendor calls** sit behind it, all multiplied by how often work is **repeated** (a retry, a double click, a redelivered message). This project attacks each factor on its own, so the savings add up instead of depending on one trick.

This page is written to be readable without an engineering background. Every row says what you get first, then how it works, and points at the code. For the engineering view of the same controls see [Guardrails and cost control](AGENTIC_AI.md#guardrails-and-cost-control) and [ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md).

**On this page**

- [The short version](#the-short-version)
- [1. Model spend: fewer, smaller, shorter calls](#1-model-spend-fewer-smaller-shorter-calls)
- [2. Vendor and data spend: do not pay twice for the same answer](#2-vendor-and-data-spend-do-not-pay-twice-for-the-same-answer)
- [3. Repeat work and abuse: one request, one run](#3-repeat-work-and-abuse-one-request-one-run)
- [4. Infrastructure spend](#4-infrastructure-spend)
- [What to optimize next](#what-to-optimize-next)

---

## The short version

| Where money leaks | What this project does about it |
|:--|:--|
| 💸 **Using a big model for everything** | A small, cheap model runs every step, and steps that need no model at all are plain code |
| 📏 **Long prompts and long tool answers** | Tool output is capped, the agents get explicit search budgets, and retrieval returns only the top three passages |
| 🔁 **Asking the same question twice** | Market data is cached, and simultaneous identical lookups collapse into one paid call |
| 🌀 **Retry storms and runaway loops** | Retries have an attempt and a time budget, a circuit breaker stops calling a failing vendor, and every agent loop has a step limit |
| 🧾 **Duplicate runs** | A repeated request returns the existing job, and a redelivered message cannot run the paid agents a second time |
| 🧪 **Development and test spend** | An offline mode blocks every paid call at the network layer, so the full test suite and CI cost nothing |
| ☁️ **Idle cloud** | One small cluster node, and the cluster can be stopped between demos |

## 1. Model spend: fewer, smaller, shorter calls

| What you get | How it works | Where |
|:--|:--|:--|
| **A lower price for every call** | Every model call uses `gpt-4o-mini`, a small and inexpensive model | [`config/llm.py`](../config/llm.py) |
| **No paying for AI where plain code is better** | The step that routes work to the researchers makes no model call at all. Market statistics (count, mean, median, range) are computed in Python and the model only classifies the market. Financial arithmetic runs in a calculator tool, which is also more accurate than asking a model to add | [`agents/supervisor.py`](../agents/supervisor.py), [`agents/market_data.py`](../agents/market_data.py) |
| **A confused agent stops instead of spending** | Each tool-using agent is limited to 10 steps, and the whole graph to 20, so a confused agent stops instead of spending | [`agents/`](../agents/), [`worker/tasks.py`](../worker/tasks.py) |
| **Research that stays within budget** | The zoning agent may make one document search, one web search and at most one page fetch; the neighborhood agent two or three lookups | [`agents/zoning_law.py`](../agents/zoning_law.py), [`agents/neighborhood_vibe.py`](../agents/neighborhood_vibe.py) |
| **No surprise bills from oversized answers** | Page fetches default to 2,000 characters (10,000 at most), web search to 3 results (5 at most), so the model cannot accidentally pull in a megabyte of text it then pays to read | [`tools/tools.py`](../tools/tools.py) |
| **Less text for the model to read and bill for** | The document search hands the model only the top 3 passages. Documents are embedded once, when the knowledge base syncs, not on every request | [`tools/vector_tools.py`](../tools/vector_tools.py), [`scripts/sync_knowledge_base.py`](../scripts/sync_knowledge_base.py) |
| **Short, predictable replies** | Each step returns a small structured object rather than free-form prose, which keeps replies short and lets a bad one fail immediately | [`schema/`](../schema/) |

## 2. Vendor and data spend: do not pay twice for the same answer

| What you get | How it works | Where |
|:--|:--|:--|
| **Repeat questions cost nothing** | A market-data lookup is remembered for 15 minutes, so a repeat costs nothing | [`cache/`](../cache/) |
| **One paid call, however many people ask at once** | If several identical lookups arrive at the same moment, only the first one goes to the vendor and the others wait for its result | [`cache/`](../cache/) |
| **No paying for doomed retries** | Only genuine hiccups (timeouts, "too busy", server errors) are retried, at most 4 attempts within 20 seconds. A request that is simply wrong is never retried | [`services/base_api_client.py`](../services/base_api_client.py) |
| **A failing vendor cannot drain the budget** | After five failures in a row, calls to that vendor stop instantly for 30 seconds instead of each one burning its own retries | [`resilience/`](../resilience/) |
| **Scheduled work never doubles the bill** | The knowledge-base sync takes a lock, so two overlapping runs cannot embed the same documents twice | [`worker/tasks.py`](../worker/tasks.py) |

## 3. Repeat work and abuse: one request, one run

| What you get | How it works | Where |
|:--|:--|:--|
| **One request, one bill** | Submitting the same request twice (a double click, a client retry) returns the first job. It does not start a second paid run | [`api/v1/reports.py`](../api/v1/reports.py) |
| **A job is never paid for twice** | Message delivery is "at least once", so the same job can arrive twice. One atomic database update decides a single winner, and the loser never runs the paid agents | [`orchestration/run_recorder.py`](../orchestration/run_recorder.py) |
| **One client cannot run up the bill** | Each caller gets a burst of 20 requests, refilling at 2 per second, so one client cannot run up the bill | [`rate_limit/`](../rate_limit/) |
| **Free development, tests and CI** | With `OFFLINE_MODE=true` any attempt to reach a paid service raises an error at the network layer, naming the code that tried. Development, tests and CI run the whole pipeline for free, and a forgotten mock shows up immediately instead of on an invoice | [`config/safety.py`](../config/safety.py) |

## 4. Infrastructure spend

| What you get | How it works | Where |
|:--|:--|:--|
| **A small, predictable cloud bill** | One small Kubernetes node (2 vCPU, 8 GiB; the Terraform file estimates about $0.10 an hour), autoscaling off, and cost-centre tags | [`terraform/main.tf`](../terraform/main.tf) |
| **Nothing billed while idle** | `az aks stop` stops the node VMs' compute billing between demos, and `az aks start` brings it back in a couple of minutes | [DEPLOYMENT.md](DEPLOYMENT.md) (hibernation section) |
| **Clean-up without losing data** | The resource group is created by hand and only referenced by Terraform, so `terraform destroy` removes the cluster but never the storage account holding the documents | [DEPLOYMENT.md](DEPLOYMENT.md) |
| **Capped memory** | Redis is capped at 256 MB with an eviction policy that can only drop expendable keys, never queued work | [`docker-compose.yml`](../docker-compose.yml) |
| **Cheaper to store and pull** | A multi-stage build ships only the runtime dependencies, so images pull faster and cost less to store | [`Dockerfile`](../Dockerfile) |

## What to optimize next

The honest list, with the reason each one is not built yet. None of these has a savings figure attached, because the project does not yet measure tokens and cost per run; that measurement is the first item.

1. **Measure it.** Per-run token and cost accounting (LangSmith or Arize Phoenix), so every other item on this list is judged by data instead of intuition.
2. **Model tiering.** Send extraction, classification and progress messages to a small model, and give only the final memo a larger one. Prerequisite: the evaluation harness, to prove the cheaper model is good enough on each step. See [Production hardening](AGENTIC_AI.md#production-hardening).
3. **Reuse whole reports.** Two identical requests within a short window could share one stored report instead of re-running the graph. It would be the largest single saving, but it needs a freshness rule, because market data changes.
4. **Skip unchanged documents in the sync.** Today each six-hour run re-embeds every PDF. A content hash per document would skip the ones that did not change.
5. **Cheaper progress messages.** A small model writes each live progress line, one call per step. Templating those lines would remove that call; the offline mode already has a deterministic template to build on.
