# Real Estate AI Investment Planner

The **Real Estate AI Investment Planner** is a state-of-the-art multi-agent platform designed to automate complex real estate investment analysis. By leveraging **LangGraph** and **LangChain** for graph-based orchestration, the application coordinates specialized AI agents that perform parallel research across market data, neighborhood sentiment, and municipal zoning laws to generate a professional-grade financial prospectus.

Built with a focus on scalability and deterministic accuracy, the system integrates the **Model Context Protocol (MCP)** for dynamic tool discovery and a sandboxed **Python REPL** for error-free financial underwriting. The entire experience is delivered through a real-time **Chainlit** interactive dashboard, providing transparency into the agents' collaborative reasoning process.

---

## 🚀 Key Features & Tech Stack

### 🧠 Advanced Multi-Agent Orchestration
*   **Parallel Research Flow:** High-efficiency architecture where researcher nodes (Market, Vibe, and Zoning) execute concurrently using **LangGraph**, significantly reducing total analysis time.
*   **Stateful Workflow Management:** Uses a global state model to pass structured context between agents, ensuring coherent synthesis of disparate data points.
*   **Reliability Guardrails:** Implements a strict `recursion_limit` for each node to prevent infinite agentic loops and ensure system stability.

### 📚 RAG Pipeline (Retrieval-Augmented Generation)
*   **Hybrid Knowledge Base:** Combines live web search with a specialized **Pinecone** vector database containing verified municipal documents.
*   **Cloud-Native ETL:** Documents are stored in **Azure Blob Storage** and synchronized to Pinecone via a custom ETL pipeline. While documents are added to Azure via a private account, they are synced using the command `uv run python scripts/sync_knowledge_base.py`.
*   **Automated Processing:** The `sync_azure_to_pinecone` function handles the end-to-end RAG lifecycle: connecting to Azure, PDF parsing (**pypdf**), semantic chunking (**RecursiveCharacterTextSplitter** with chunk size 1000 and overlap 100), and high-performance embedding generation (**OpenAI text-embedding-3-small**) before pushing to the vector database.
*   **Expert Prompting:** The Zoning Law agent uses optimized system instructions to prioritize these verified records over web fallbacks, ensuring data integrity while maintaining a strict "search budget" for performance.

### 🛠️ Dynamic Tooling & MCP Integration
*   **Standardized Extensibility:** Uses the **UnifiedMCPGateway** to dynamically discover and load tools from **MCP Servers** (Brave Search, Fetch, OpenStreetMap, Wikipedia).
*   **Easy Expansion:** Adding new capabilities is as simple as updating the `MCP_SERVER_REGISTRY` in `tools.py`.
*   **Deterministic Underwriting:** The Financial Modeler agent uses a sandboxed **Python REPL** to calculate Cap Rates, NOI, and Cash-on-Cash returns with 100% mathematical precision.

### 💎 Premium User Experience
*   **Human-Readable Logging:** Features the `@lmm_translator` agent, which converts raw technical logs and JSON payloads into sleek, emoji-enhanced UI messages in real-time.
*   **Customized Interface:** A tailored **Chainlit** UI with custom CSS (`style.css`) and a dark-themed `theme.json` for a professional, investor-centric look.
*   **Action Tracking:** Every tool usage and node action is populated in the UI side-panel, providing a clear "audit trail" of the AI's research.

### 🛡️ Robust Engineering
*   **Type-Safe Contracts:** Utilizes **Pydantic** for structured output and state management. Field descriptions (e.g., `zpid` for property IDs) are used to improve LLM extraction accuracy and provide clear API documentation.
*   **Token Economy:** Implements custom optimizations in `tools.py` to sanitize schemas and inject restrictive defaults (lower search counts/fetch lengths), minimizing token consumption and API costs.
*   **Modular Architecture:** Uses a `BaseAPIClient` structure, making it trivial to plug in new data providers like Zillow, Redfin, or proprietary real estate APIs.

---

## 🤖 Agent Architecture & Roles

The complete graph topology and node definitions are maintained in `graph.py`.

1.  **Ingest Agent:** Parses raw user prompts into structured criteria (City, Budget, Strategy).
2.  **Supervisor Agent:** Orchestrates the research phase, routing the workflow based on the extracted intent.
3.  **Market Data Agent:** Retrieves live listings and pricing telemetry via **RapidAPI**.
4.  **Neighborhood Vibe Agent:** Analyzes community sentiment and transit connectivity using **OpenStreetMap** and **Wikipedia**.
5.  **Zoning Law Agent:** Queries the **RAG** database and web for land-use restrictions and Short-Term Rental (STR) regulations.
6.  **Financial Modeler Agent:** The "Synthesizer" that executes code-based financial modeling and generates the final markdown prospectus.

---

## 🛠️ Installation & Setup

### Prerequisites
*   Python 3.12+ (managed via **uv**)
*   Docker & Docker Compose (optional)

### Configuration
Create a `.env` file in the root directory with the following variables:

```env
# Core API Keys
OPENAI_API_KEY=your_key
RAPIDAPI_KEY=your_key
BRAVE_API_KEY=your_key
GH_TOKEN=your_github_token
PINECONE_API_KEY=your_key
PINECONE_INDEX_NAME=real-estate-app

# Azure RAG Configuration
AZURE_STORAGE_CONNECTION_STRING=your_connection_string

# Testing & Mocking (Toggles)
MOCK_FINANCIAL_MODELER_AGENT_OUTPUT=False
MOCK_INGEST_INPUT_AGENT_OUTPUT=False
MOCK_MARKET_DATA_AGENT_OUTPUT=False
MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT=False
MOCK_ZONING_LAW_AGENT_OUTPUT=False
MOCK_MARKET_DATA_API=False

# System
DEBUG_MODE=False
```

### Option 1: Running via CLI (Recommended for Development)
```bash
# Sync Knowledge Base (RAG)
uv run python scripts/sync_knowledge_base.py

# Run Web Interface
uv run chainlit run app.py -w

# Run CLI Mode
uv run python cli.py
```

### Option 2: Running via Docker
```bash
# Using Docker Compose
docker-compose up --build

# Using Standard Docker Commands
docker build -t realestateapp:local .
docker run -p 8080:8080 --env-file .env realestateapp:local
```
*Access the app at `http://localhost:8080/`*

---

## 🚢 Deployment & CI/CD

*   **Cloud Infrastructure:** Fully deployed on **Microsoft Azure** using **Azure Container Apps**.
*   **CI/CD Pipeline:** GitHub Actions (`deploy.yml`) automatically triggers on push, running unit tests and deploying fresh Docker images to **GitHub Packages** for seamless revision management.
*   **Custom Domain:** Accessible via [https://realestateapp.marekpdev.com/](https://realestateapp.marekpdev.com/).

---

## 📈 Optimization Strategies

*   **Prompt Engineering:** Agents are primed with efficiency-first instructions (e.g., "Good enough" criteria for zoning research) to minimize LLM latency and tool usage.
*   **Cost Efficiency:** Schema sanitization removes redundant JSON metadata before sending to OpenAI.
*   **Smart Fallbacks:** The Zoning agent is instructed to prioritize local RAG data before falling back to expensive web searches.

---

## 🔮 Further Improvements

*   **Infrastructure as Code (IaC):** Implement **Terraform** for reproducible Azure environment setup.
*   **Orchestration:** Migration to **Kubernetes** (AKS) for enterprise-scale auto-scaling.
*   **Observability:** Integration of **LangSmith** or **Arize Phoenix** for deeper trace analysis and evaluation.
*   **Cost Optimization:** Implementation of semantic caching for common market queries to further reduce API spend.
