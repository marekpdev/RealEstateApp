"""Generates every diagram image in docs/ (light and dark SVG) from one description each.

    python docs/images/generate.py            # all diagrams
    python docs/images/generate.py agent-graph hybrid-retrieval

Writes <name>-light.svg and <name>-dark.svg next to this file. The docs embed them with a
<picture> element, so GitHub shows the variant that matches the reader's theme. Pure
standard library (see svgkit.py). Sequence diagrams and the data-model ER diagram stay
in Mermaid, where the notation itself is the point.
"""
from __future__ import annotations

import sys
from pathlib import Path

from svgkit import Canvas, THEMES, hcurve, vcurve

DIAGRAMS: dict = {}


def diagram(name: str, width: int, height: int, aria: str, check: bool = True):
    def register(fn):
        DIAGRAMS[name] = (width, height, aria, check, fn)
        return fn
    return register


# ---------------------------------------------------------------------------------------------
# System architecture (the README hero, also the overview in ARCHITECTURE.md)
# ---------------------------------------------------------------------------------------------
@diagram(
    "architecture", 1200, 770,
    "System architecture: clients reach FastAPI through NGINX; FastAPI records jobs in PostgreSQL "
    "and enqueues work in Redis; a Celery worker running the LangGraph agents consumes tasks, writes audit rows and "
    "publishes progress events that stream back to the client over Server-Sent Events.",
    check=False)
def architecture(c: Canvas):
    t, W = c.t, c.W
    c.band(14, 16, W - 28, 200)
    c.band(14, 236, W - 28, 150)
    c.band(14, 406, W - 28, 348)
    c.text(1172, 52, "REQUEST PATH", 13, 700, t["head"], "end", 'letter-spacing="2"')
    c.text(1172, 72, "fast · stateless", 14, 400, t["sub"], "end")
    c.text(1172, 92, "never runs an agent", 14, 400, t["sub"], "end")
    c.text(1172, 272, "SHARED STATE", 13, 700, t["head"], "end", 'letter-spacing="2"')
    c.text(1172, 292, "the only thing API", 14, 400, t["sub"], "end")
    c.text(1172, 311, "and worker share", 14, 400, t["sub"], "end")
    c.text(44, 440, "WORK PATH", 13, 700, t["head"], "start", 'letter-spacing="2"')
    c.text(150, 440, "slow · expensive · retryable", 14, 400, t["sub"], "start")

    # request path
    c.card(44, 74, 200, 108, "llm", "Clients", ["Chainlit chat UI", "curl · services"])
    c.card(300, 74, 190, 108, "edge", "NGINX", ["reverse proxy", "SSE unbuffered"])
    c.card(546, 60, 470, 136, "api", "FastAPI  ·  /api/v1", ["JWT / API key · CORS · rate limit", "Idempotency-Key  ·  SSE stream", "OpenAPI / Swagger"])
    c.arrow("M244 128 L296 128")
    c.arrow("M490 128 L542 128")
    c.arrow("M780 60 C780 26 144 26 144 70", dashed=True)
    c.label(462, 34, "SSE: live agent steps")

    # shared state
    c.card(250, 268, 330, 92, "data", "PostgreSQL", ["durable truth: jobs · reports", "agent audit trail · full-text index"])
    c.card(640, 268, 330, 92, "data", "Redis", ["ephemeral coordination", "queue · pub/sub · cache · rate limits"])
    c.arrow("M600 196 C600 232 470 232 470 264")
    c.label(492, 226, "claim job")
    c.arrow("M800 196 L800 264")
    c.label(800, 232, "enqueue")
    c.arrow("M930 196 L930 264", dashed=True)
    c.label(930, 232, "subscribe")

    # work path
    c.band(40, 456, 760, 270, rx=16, tint="compute")
    c.text(60, 484, "Celery worker  ·  LangGraph multi-agent graph", 17, 700, t["text"], "start")
    c.text(60, 504, "typed Pydantic state between every node", 14, 400, t["sub"], "start")
    c.card(62, 566, 112, 62, "llm", "Ingest", ["LLM → typed"], 12, 16)
    c.card(206, 566, 122, 62, "edge", "Supervisor", ["no LLM call"], 12, 16)
    c.card(372, 518, 190, 50, "llm", "Market Data", [], 12, 16)
    c.card(372, 578, 190, 50, "compute", "Neighborhood Vibe", [], 12, 16)
    c.card(372, 638, 190, 50, "compute", "Zoning Law · RAG", [], 12, 16)
    c.card(606, 566, 172, 62, "compute", "Financial Modeler", ["Python REPL tool"], 12, 16)
    c.arrow("M174 597 L202 597")
    c.arrow("M328 597 C350 597 350 543 368 543")
    c.arrow("M328 597 L368 603")
    c.arrow("M328 597 C350 597 350 663 368 663")
    c.arrow("M562 543 C586 543 584 577 602 577")
    c.arrow("M562 603 C584 603 584 597 602 597")
    c.arrow("M562 663 C586 663 584 617 602 617")
    c.text(467, 706, "three researchers run in parallel", 13.5, 600, t["sub"])
    c.card(62, 646, 240, 60, "compute", "Celery Beat", ["scheduled RAG sync"], 12, 15)

    c.card(850, 470, 300, 240, "ext", "External services", ["OpenAI  ·  Pinecone", "MCP tool servers", "(Brave · Fetch · OSM · Wikipedia)", "RapidAPI  ·  Azure Blob", "", "retry · circuit breaker · cache-aside"])
    c.arrow("M778 597 L846 597")

    # state <-> worker
    c.arrow("M415 360 L415 452")
    c.label(415, 412, "status + audit rows", 168)
    c.arrow("M760 360 L760 452", both=True)
    c.label(760, 412, "consume tasks · publish progress", 250)


# ---------------------------------------------------------------------------------------------
# Highlights strip (README)
# ---------------------------------------------------------------------------------------------
HIGHLIGHTS = [  # (accent colour, big number, label, two caption lines)
    ("compute", "3", "agents in parallel", ["market, area and zoning", "researched at the same time"]),
    ("llm", "4", "MCP tool servers", ["live web, maps and", "Wikipedia data"]),
    ("api", "2", "search methods", ["meaning and keywords,", "combined for accuracy"]),
    ("data", "240+", "automated tests", ["run on a real database", "and message queue"]),
    ("edge", "34", "design decisions", ["documented with", "their trade-offs"]),
    ("ok", "$0", "to run offline", ["the whole pipeline,", "no API keys needed"]),
]


@diagram(
    "highlights", 1000, 250,
    "Six highlights of the project: 3 agents research in parallel; 4 MCP tool servers give live web, map and "
    "Wikipedia data; 2 search methods, meaning and keywords, are combined; 240 plus automated tests run on a real "
    "database and message queue; 34 documented design decisions; and $0 to run the whole pipeline offline.")
def highlights(c: Canvas):
    from svgkit import est_width
    t = c.t
    w, h, gx, gy = 316, 108, 22, 18
    for i, (kind, number, label, caption) in enumerate(HIGHLIGHTS):
        x = 4 + (i % 3) * (w + gx)
        y = 4 + (i // 3) * (h + gy)
        c.out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{t["band"]}" stroke="{t["band_stroke"]}" stroke-width="1.5"/>')
        c.out.append(f'<rect x="{x + 16}" y="{y + 22}" width="5" height="{h - 44}" rx="2.5" fill="{t[kind][1]}"/>')
        c.text(x + 36, y + 68, number, 44, 800, t["text"], "start")
        tx = x + 36 + est_width(number, 44, True) + 16
        c.text(tx, y + 44, label, 16, 700, t["text"], "start")
        c.note(tx, y + 66, caption, size=13.5, gap=19)
        if c.check and est_width(label, 16, True) * 1.10 > x + w - 12 - tx:
            c.warnings.append(f"[{c.name}] label '{label}' may overflow its tile")


# ---------------------------------------------------------------------------------------------
# The agent graph (AGENTIC_AI.md)
# ---------------------------------------------------------------------------------------------
@diagram(
    "agent-graph", 1100, 800,
    "The agent graph: an Ingest step turns free text into a typed city and budget; a supervisor with no LLM call "
    "fans out to three researchers that run in parallel (Market Data, Neighborhood Vibe, Zoning Law); a Financial "
    "Modeler joins their results and writes the investor report.")
def agent_graph(c: Canvas):
    t = c.t
    c.card(375, 24, 350, 52, "edge", "\u201cMulti-family in Austin, max $900k\u201d", rx=26, title_size=15)
    c.arrow("M550 76 L550 108")
    c.card(375, 110, 350, 80, "llm", "1 \u00b7 Ingest", ["LLM \u2192 typed Pydantic output", "city \u00b7 budget"])
    c.arrow("M550 190 L550 222")
    c.card(375, 224, 350, 80, "edge", "2 \u00b7 Supervisor", ["static fan-out, no LLM call", "a graph edge already knows the route"])

    c.heading(30, 130, "TYPED STATE")
    c.note(30, 154, ["All six nodes share one Pydantic state.", "A malformed model answer fails at the", "node boundary, not three nodes later."])
    c.heading(770, 130, "COLOUR KEY")
    for i, (kind, s) in enumerate([("edge", "plain code, no LLM call"), ("llm", "one LLM call, structured output"), ("compute", "tool-calling agent loop")]):
        c.swatch(770, 146 + i * 30, kind)
        c.text(800, 160 + i * 30, s, 14, 400, t["sub"], "start")

    c.band(20, 344, 1060, 232)
    c.heading(1062, 564, "RUN IN PARALLEL", "end")
    for x, sx in ((190, 490), (550, 550), (910, 610)):
        c.arrow(vcurve(sx, 304, x, 388))
    c.label(550, 338, "fan-out")
    c.card(30, 390, 320, 150, "llm", "3a \u00b7 Market Data", ["listings API + one LLM classifier", "statistics computed in Python", "cache \u00b7 breaker \u00b7 retries"])
    c.card(390, 390, 320, 150, "compute", "3b \u00b7 Neighborhood Vibe", ["tool-calling agent", "Wikipedia + OpenStreetMap", "discovered at runtime over MCP"])
    c.card(750, 390, 320, 150, "compute", "3c \u00b7 Zoning Law", ["tool-calling agent", "RAG first: Pinecone + Postgres", "web search only as a fallback"])

    for x, ex in ((190, 490), (550, 550), (910, 610)):
        c.arrow(vcurve(x, 540, ex, 604))
    c.label(550, 580, "join: waits for all three")
    c.card(375, 606, 350, 80, "compute", "4 \u00b7 Financial Modeler", ["tool-calling agent + Python REPL", "the model delegates the math to code"])
    c.arrow("M550 686 L550 718")
    c.card(375, 720, 350, 62, "edge", "Investor-grade report", ["saved in Postgres \u00b7 streamed live"], rx=24)


# ---------------------------------------------------------------------------------------------
# Knowledge-base ingestion (AGENTIC_AI.md)
# ---------------------------------------------------------------------------------------------
@diagram(
    "knowledge-base-sync", 1100, 420,
    "Knowledge-base sync: Celery Beat starts a locked sync task every six hours; it reads zoning-law PDFs from Azure "
    "Blob Storage, parses and chunks them, then writes the same chunks first to PostgreSQL full-text search and second "
    "to Pinecone embeddings.")
def kb_sync(c: Canvas):
    t = c.t
    y, h, my = 40, 96, 88
    c.card(30, y, 170, h, "compute", "Celery Beat", ["every 6 hours"])
    c.arrow(f"M200 {my} L246 {my}")
    c.card(250, y, 250, h, "compute", "Sync task", ["Redis lock (SET NX EX):", "an overlapping run is a no-op"])
    c.arrow(f"M500 {my} L556 {my}")
    c.card(560, y, 210, h, "ext", "Azure Blob", ["zoning-law PDFs"])
    c.arrow(f"M770 {my} L826 {my}")
    c.card(830, y, 240, h, "compute", "Parse and chunk", ["pypdf → text → splitter", "1000 chars, 100 overlap"])

    c.arrow(vcurve(920, 136, 685, 246))
    c.label(770, 198, "1st: the same chunks", 172)
    c.arrow(vcurve(990, 136, 955, 246))
    c.label(1010, 198, "2nd: the same chunks", 172)
    c.card(560, 250, 250, 100, "data", "PostgreSQL", ["document_chunks", "tsvector + GIN index"])
    c.card(840, 250, 230, 100, "data", "Pinecone", ["OpenAI embeddings", "behind a circuit breaker"])

    c.heading(30, 262, "WHY IT IS BUILT THIS WAY")
    c.bullets(30, 288, [["Same chunks in both indexes, so their", "results can be compared and fused."],
                        ["Postgres first, always: a Pinecone outage", "never leaves the lexical index stale."],
                        ["Full replace in one transaction: a document", "removed at the source leaves search too."]])


# ---------------------------------------------------------------------------------------------
# Hybrid retrieval (AGENTIC_AI.md)
# ---------------------------------------------------------------------------------------------
@diagram(
    "hybrid-retrieval", 1100, 640,
    "Hybrid retrieval: a zoning question runs in parallel against dense Pinecone search and lexical PostgreSQL "
    "full-text search; Reciprocal Rank Fusion merges the two ranked lists and the top three chunks go to the agent.")
def hybrid_retrieval(c: Canvas):
    t = c.t
    c.card(375, 24, 350, 56, "edge", "Zoning question from the agent", rx=28, title_size=16)
    c.arrow(vcurve(480, 80, 280, 152))
    c.arrow(vcurve(620, 80, 820, 152))
    c.label(550, 122, "in parallel")
    c.card(90, 154, 380, 110, "llm", "Dense · Pinecone", ["embeddings match meaning", "finds paraphrases"])
    c.card(630, 154, 380, 110, "api", "Lexical · Postgres full-text", ["tsvector + GIN · ts_rank_cd", "finds exact terms: ordinance numbers"])
    c.arrow(vcurve(280, 264, 470, 350))
    c.label(340, 316, "ranked list")
    c.arrow(vcurve(820, 264, 630, 350))
    c.label(760, 316, "ranked list")
    c.card(300, 352, 500, 116, "compute", "Reciprocal Rank Fusion", ["score(d) = Σ 1 / (k + rank),   k = 60", "ranks, not scores: no normalising needed"])
    c.arrow("M550 468 L550 508")
    c.card(300, 510, 500, 104, "edge", "Top 3 chunks go to the agent", ["with source, rrf_score and each list's rank", "if one side is down, the other still answers"])

    c.heading(30, 376, "WHY BOTH HALVES")
    c.note(30, 402, ["Dense matches meaning but can", "miss a rare exact token. Lexical", "nails exact terms but misses", "paraphrases."], gap=20)
    c.heading(830, 376, "WHY RANK FUSION")
    c.note(830, 402, ["The two score scales are not", "comparable, so RRF adds up", "ranks instead of scores."], gap=20)


# ---------------------------------------------------------------------------------------------
# Resilient vendor call (ARCHITECTURE.md)
# ---------------------------------------------------------------------------------------------
@diagram(
    "resilient-vendor-call", 1100, 760,
    "Every paid vendor call passes four layers in order: a Redis cache-aside, a single-flight lock, a per-vendor "
    "circuit breaker and a jittered retry loop. A cache hit costs nothing, an open breaker fails without a network "
    "call, and a bad request fails at once without counting against the breaker.")
def resilient_call(c: Canvas):
    t = c.t
    x, w = 40, 430
    c.card(x, 24, w, 48, "edge", "Agent asks for market data", rx=24, title_size=15)
    c.arrow("M255 72 L255 110")
    c.card(x, 112, w, 74, "data", "1 · Cache-aside", ["Redis · hashed query key · 15 min TTL"])
    c.arrow("M255 186 L255 238")
    c.label(255, 216, "miss")
    c.card(x, 240, w, 90, "data", "2 · Single-flight lock", ["SET NX EX: N concurrent misses make 1 call", "the others wait for the cache to fill"])
    c.arrow("M255 330 L255 376")
    c.card(x, 378, w, 74, "data", "3 · Circuit breaker (one per vendor)", ["5 failures in a row open it · a probe after 30 s"])
    c.arrow("M255 452 L255 498")
    c.label(255, 480, "closed, or a probe")
    c.card(x, 500, w, 90, "data", "4 · Retry loop", ["only timeouts · 429 · 5xx", "full-jitter backoff · max 4 attempts · 20 s budget"])
    c.arrow("M255 590 L255 634")
    c.card(x, 636, w, 52, "ext", "Vendor (RapidAPI)", rx=26, title_size=15)

    rx_, rw = 650, 420
    c.arrow("M470 149 L646 149")
    c.label(558, 143, "hit")
    c.card(rx_, 122, rw, 54, "ok", "Answered: no vendor call, no cost", rx=12, title_size=15)
    c.arrow("M470 415 L646 415")
    c.label(558, 409, "breaker open")
    c.card(rx_, 388, rw, 54, "bad", "Fails in microseconds: no network call", rx=12, title_size=15)
    c.arrow("M470 545 L646 545")
    c.label(558, 539, "budget exhausted")
    c.card(rx_, 518, rw, 54, "bad", "Breaker failure recorded, then 503 or 429", rx=12, title_size=15)
    c.arrow(hcurve(470, 654, 646, 630))
    c.label(558, 636, "200")
    c.card(rx_, 604, rw, 50, "ok", "Cached, lock released", rx=12, title_size=15)
    c.arrow(hcurve(470, 672, 646, 704))
    c.label(558, 698, "other 4xx")
    c.card(rx_, 676, rw, 56, "bad", "Fails at once: caller's fault, breaker unaffected", rx=12, title_size=15)


# ---------------------------------------------------------------------------------------------
# Compose startup order (ARCHITECTURE.md)
# ---------------------------------------------------------------------------------------------
@diagram(
    "compose-startup", 1170, 470,
    "Docker Compose startup order in four waves: PostgreSQL and Redis become healthy; a one-shot migration job "
    "brings the schema up to date and exits; the web app, worker and Celery Beat start; NGINX starts last, once the "
    "web app is healthy, and is the only way in.")
def compose_startup(c: Canvas):
    t = c.t
    for x, s in ((30, "1 · INFRASTRUCTURE"), (300, "2 · SCHEMA"), (640, "3 · APPLICATION"), (990, "4 · EDGE")):
        c.heading(x, 44, s)
    c.card(30, 64, 180, 96, "data", "postgres", ["PostgreSQL 16", "pg_isready check"])
    c.card(30, 196, 180, 96, "data", "redis", ["Redis 7", "PING check"])
    c.card(300, 64, 230, 96, "compute", "migrate", ["alembic upgrade head", "one-shot: exits when done"])
    c.card(640, 64, 260, 96, "api", "web_app", ["FastAPI + Chainlit UI", "needs: DB · Redis · migration"])
    c.card(640, 196, 260, 96, "compute", "worker", ["Celery + the agent graph", "needs: DB · Redis · migration"])
    c.card(640, 328, 260, 96, "compute", "beat", ["Celery Beat, exactly one replica", "needs: Redis only"])
    c.card(990, 64, 150, 96, "edge", "nginx", ["the only way in", "host port 8080"])

    c.arrow("M210 112 L296 112")
    c.label(253, 103, "healthy", 62)
    c.arrow("M530 112 L636 112")
    c.label(583, 103, "migrated", 72)
    c.arrow(hcurve(530, 140, 636, 236))
    c.arrow("M900 112 L986 112")
    c.label(943, 103, "healthy", 62)
    c.note(1065, 190, ["nginx starts once", "web_app is healthy"], anchor="middle")
    c.note(30, 340, ["Compose enforces this order with", "depends_on conditions:", "service_healthy and", "service_completed_successfully."], gap=20)


# ---------------------------------------------------------------------------------------------
# CI/CD pipeline (ARCHITECTURE.md)
# ---------------------------------------------------------------------------------------------
@diagram(
    "ci-cd-pipeline", 1120, 400,
    "CI/CD: a push or pull request to main runs the test job against real PostgreSQL 16 and Redis 7 service "
    "containers; only if it passes, the build job pushes a multi-stage image to GHCR. Deploying to Azure AKS, "
    "provisioned by Terraform, is a deliberate manual kubectl apply.")
def ci_cd(c: Canvas):
    t = c.t
    y, h, my = 50, 110, 105
    c.card(30, y, 180, h, "edge", "Trigger", ["push or pull request", "to main"])
    c.arrow(f"M210 {my} L256 {my}")
    c.card(260, y, 230, h, "llm", "Test job", ["real Postgres 16 + Redis 7", "alembic upgrade head", "pytest"])
    c.arrow(f"M490 {my} L596 {my}")
    c.label(543, my - 10, "needs: test", 100)
    c.card(600, y, 220, h, "llm", "Build and push", ["multi-stage Docker image", "non-root runtime"])
    c.arrow(f"M820 {my} L886 {my}")
    c.label(853, my - 10, "pushes", 58)
    c.card(890, y, 200, h, "data", "GHCR", ["container registry", "latest · pr-N tags"])

    c.arrow("M990 160 L990 256", dashed=True)
    c.label(990, 212, "manual: kubectl apply", 186)
    c.card(890, 260, 200, 90, "compute", "Azure AKS", ["Kubernetes cluster", "web tier deployed"])
    c.card(580, 260, 210, 90, "compute", "Terraform", ["infrastructure as code", "azurerm provider"])
    c.arrow("M790 305 L886 305")
    c.label(838, 293, "creates", 64)

    c.heading(30, 282, "NOTHING DEPLOYS ITSELF")
    c.note(30, 308, ["CI ends at the registry. Rolling out is a", "deliberate manual step (kubectl apply),", "documented in DEPLOYMENT.md."], gap=20)


# ---------------------------------------------------------------------------------------------
# Job lifecycle (ARCHITECTURE.md)
# ---------------------------------------------------------------------------------------------
@diagram(
    "job-lifecycle", 1080, 430,
    "Job lifecycle: POST /reports creates a pending job; a worker's atomic claim moves it to running; it ends "
    "completed when the report is saved, or failed when an agent raised. A failed job can run again by a same-key "
    "replay or a manual replay from the dead-letter queue; when retries are exhausted the task is parked there.")
def job_lifecycle(c: Canvas):
    t = c.t
    y, h, my = 110, 76, 148
    c.dot(40, my)
    c.arrow(f"M56 {my} L196 {my}")
    c.label(126, my - 8, "POST /reports", 118)
    c.card(200, y, 160, h, "data", "pending", ["queued"])
    c.arrow(f"M360 {my} L506 {my}")
    c.label(433, my - 8, "worker claims it", 142)
    c.card(510, y, 160, h, "compute", "running", ["a worker owns it"])
    c.arrow(f"M670 {my} L816 {my}")
    c.label(743, my - 8, "report saved", 112)
    c.card(820, y, 160, h, "ok", "completed", ["terminal"])
    c.arrow(f"M980 {my} L1018 {my}")
    c.dot(1038, my, ring=True)

    c.card(510, 300, 160, h, "bad", "failed", ["error recorded"])
    c.arrow("M560 186 L560 296")
    c.text(548, 246, "an agent raised", 13.5, 600, t["sub"], "end")
    c.arrow("M620 296 L620 190")
    c.text(632, 240, "same-key replay, or", 13.5, 600, t["sub"], "start")
    c.text(632, 258, "a manual replay from the DLQ", 13.5, 600, t["sub"], "start")
    c.arrow("M670 338 L816 338")
    c.label(743, 330, "retries exhausted", 140)
    c.card(820, 300, 210, h, "ext", "dead-letter queue", ["payload kept for replay"])

    c.heading(30, 318, "ENFORCED BY SQL")
    c.note(30, 344, ["The only way into running is one atomic", "UPDATE … WHERE status IN ('pending','failed')", "RETURNING id. Whoever gets a row back owns the run."], gap=20)


# ---------------------------------------------------------------------------------------------
# Circuit breaker (ARCHITECTURE.md)
# ---------------------------------------------------------------------------------------------
@diagram(
    "circuit-breaker", 1000, 430,
    "Circuit breaker states: Closed lets calls through and counts failures; five failures in a row open it; Open "
    "fails every call instantly with no network call; after 30 seconds it goes half-open and allows one probe call, "
    "which closes it if it succeeds and re-opens it, restarting the timer, if it fails.")
def circuit_breaker(c: Canvas):
    t = c.t
    c.dot(50, 96)
    c.arrow("M60 104 L100 138")
    c.card(40, 140, 240, 104, "ok", "Closed", ["calls flow through", "failures are counted"])
    c.card(720, 140, 240, 104, "bad", "Open", ["fail fast: no network call", "503 in microseconds"])
    c.card(380, 300, 240, 96, "data", "Half-open", ["exactly one probe call", "is let through"])

    c.arrow("M160 138 C160 44 840 44 840 136")
    c.label(500, 74, "5 failures in a row", 160)
    c.arrow("M800 246 C800 320 720 340 624 348")
    c.label(742, 326, "after 30 s", 90)
    c.arrow("M596 298 C600 250 650 222 716 218")
    c.text(640, 276, "probe fails:", 13.5, 600, t["sub"], "start")
    c.text(640, 294, "timer restarts", 13.5, 600, t["sub"], "start")
    c.arrow("M376 348 C250 350 160 320 160 248")
    c.label(258, 336, "probe succeeds", 130)


# ---------------------------------------------------------------------------------------------
# Retrieval evaluation chart (AGENTIC_AI.md)
# ---------------------------------------------------------------------------------------------
# Values come from `uv run python scripts/evaluate_retrieval.py --demo-dense` (nDCG@3). The dense arm
# there is a hand-authored stand-in, so the chart says so in its own subtitle. tests/test_retrieval_evaluation.py
# pins these numbers, so the chart cannot silently go stale.
EVAL_NDCG = {
    "Exact-term queries": {"lexical-only": 0.903, "dense-only": 0.815, "hybrid (RRF)": 1.000},
    "Paraphrased queries": {"lexical-only": 0.000, "dense-only": 1.000, "hybrid (RRF)": 1.000},
}
# Categorical slots 1-3 of the validated data-viz palette (all-pairs colour-blind safe in both modes).
EVAL_COLORS = {
    "light": {"lexical-only": "#1baf7a", "dense-only": "#2a78d6", "hybrid (RRF)": "#eb6834"},
    "dark": {"lexical-only": "#199e70", "dense-only": "#3987e5", "hybrid (RRF)": "#d95926"},
}


def _hbar(x, y, w, h, r=4):
    """A horizontal bar: square at the baseline, rounded at the data end."""
    if w <= 0:
        return ""
    r = min(r, w / 2, h / 2)
    return (f"M{x} {y} H{x + w - r} A{r} {r} 0 0 1 {x + w} {y + r} V{y + h - r} "
            f"A{r} {r} 0 0 1 {x + w - r} {y + h} H{x} Z")


@diagram(
    "retrieval-eval", 1000, 470,
    "Grouped bar chart of nDCG at 3 by query type for three retrieval methods, from an illustrative run on eight "
    "hand-labelled queries with a stand-in dense arm. Exact-term queries: lexical-only 0.903, dense-only 0.815, hybrid "
    "1.000. Paraphrased queries: lexical-only 0.000, dense-only 1.000, hybrid 1.000. Each single method has a blind spot; "
    "the fused hybrid covers both.")
def retrieval_eval(c: Canvas):
    t = c.t
    colors = EVAL_COLORS[c.name]
    x0, x1 = 300, 860
    X = lambda v: x0 + (x1 - x0) * v

    c.text(40, 40, "nDCG@3 by query type", 19, 700, t["text"], "start")
    c.text(40, 63, "Illustrative: 8 hand-labelled queries. The dense arm is a hand-authored stand-in, not Pinecone.", 13.5, 400, t["sub"], "start")

    lx = 40
    for name, desc in (("lexical-only", "Postgres full-text"), ("dense-only", "embeddings (stand-in)"), ("hybrid (RRF)", "both, fused")):
        c.out.append(f'<rect x="{lx}" y="86" width="14" height="14" rx="3" fill="{colors[name]}"/>')
        label = f"{name}: {desc}"
        c.text(lx + 22, 98, label, 13.5, 400, t["sub"], "start")
        lx += 22 + int(len(label) * 7.0) + 26

    top, bottom = 128, 424
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        col = t["head"] if v == 0 else t["band_stroke"]
        c.out.append(f'<line x1="{X(v)}" y1="{top}" x2="{X(v)}" y2="{bottom}" stroke="{col}" stroke-width="1"/>')
        c.text(X(v), bottom + 22, ("0" if v == 0 else f"{v:g}"), 12.5, 400, t["sub"])

    gy = 142
    for group, rows in EVAL_NDCG.items():
        c.text(40, gy + 14, group, 15, 700, t["text"], "start")
        for i, (name, v) in enumerate(rows.items()):
            y = gy + 26 + i * 36
            c.text(x0 - 14, y + 17, name, 14, 400, t["sub"], "end")
            d = _hbar(x0, y, X(v) - x0, 24)
            if d:
                c.out.append(f'<path d="{d}" fill="{colors[name]}"/>')
            c.text(X(v) + 10, y + 17, f"{v:.3f}", 14, 600, t["text"], "start")
            if v == 0:
                c.text(X(v) + 62, y + 17, "finds nothing when the words differ", 13.5, 400, t["sub"], "start")
        gy += 148


# ---------------------------------------------------------------------------------------------
def build(name: str, theme: str) -> tuple[str, list[str]]:
    width, height, aria, check, draw = DIAGRAMS[name]
    canvas = Canvas(theme, width, height, aria, check=check)
    draw(canvas)
    return canvas.svg(), canvas.warnings


def main(argv: list[str]) -> int:
    here = Path(__file__).parent
    names = argv or list(DIAGRAMS)
    unknown = [n for n in names if n not in DIAGRAMS]
    if unknown:
        print("unknown diagram(s):", ", ".join(unknown), "| known:", ", ".join(DIAGRAMS))
        return 2
    warned = False
    for name in names:
        for theme in THEMES:
            svg, warnings = build(name, theme)
            (here / f"{name}-{theme}.svg").write_text(svg, encoding="utf-8")
            for w in warnings:
                warned = True
                print("  warning:", name, w)
        print("wrote", f"{name}-light.svg", f"{name}-dark.svg")
    return 1 if warned else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
