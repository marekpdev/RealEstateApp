# 🏢 Real Estate AI Investment Planner

Welcome! This system is a multi-agent orchestrator built with **LangGraph**. It evaluates a real estate deal by researching three angles in parallel and merging them into one investment report.

### 🧠 How It Works Under the Hood
Each request runs as a background job on a worker, and every step streams back here live:

1. **Ingest Gateway**: An LLM extracts your parameters (location, budget bounds) into a validated Pydantic schema.
2. **Supervisor**: A plain fan-out (no LLM involved) that starts the three researchers at the same time.
3. **Researchers (run in parallel)**:
   - **Market Data Agent**: Pulls live listings from a real-estate API, computes aggregate statistics in code, then asks an LLM to classify the market.
   - **Neighborhood Vibe Agent**: Uses tools discovered at runtime over **MCP** (Wikipedia and OpenStreetMap) to describe the area.
   - **Zoning Law Agent**: Retrieves zoning rules with **RAG** over a Pinecone index (optionally hybrid with Postgres full-text search) and falls back to web search when the knowledge base is thin.
4. **Financial Modeler**: Merges the three results, computes the deal metrics with a Python REPL tool, and writes the final markdown report.

### 🚀 Try It Out
Type an investment scenario into the chat bar below to watch the underlying LangGraph nodes activate in real time.

**Example Prompts to Paste:**
* *"I would like to invest in Los Angeles, CA, and my max budget is $800,000. Calculate deal metrics."*
* *"Crunch investment metrics for a property in Hollywood Hills under $800,000."*