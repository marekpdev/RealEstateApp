# Real Estate AI Investment Planner

The **Real Estate AI Investment Planner** is a **Python**-based multi-agent platform that automates real estate investment analysis to deliver professional investment reports. Built on **LangGraph** and **LangChain** for multi-agent coordination, the system leverages **OpenAI LLMs** for advanced reasoning across specialized agents performing parallel market research, sentiment analysis, and financial modeling. The backend integrates **FastAPI** endpoints, the **Model Context Protocol (MCP)** for dynamic tool discovery, and a **RAG pipeline** utilizing a **Pinecone vector database** and **Azure Blob Storage**. Containerized with **Docker** and deployed on **Microsoft Azure Container Apps**, this platform efficiently transforms raw market data into actionable investment insights.

**Live Demo:** [https://realestateapp.marekpdev.com/](https://realestateapp.marekpdev.com/)

<video src="https://github.com/user-attachments/assets/e4cf66d4-9918-41ad-977a-a42407e43d1b" controls width="100%">
  Your browser does not support the video tag.
</video>

---

## 🚀 Key Features & Tech Stack

### 🧠 Advanced Multi-Agent Orchestration
*   **Parallel Graph Execution:** High-concurrency architecture where researcher nodes (Market, Vibe, and Zoning) execute in parallel using **LangGraph**'s state-machine orchestration, significantly reducing total analysis time.
*   **Stateful Workflow Management:** Uses **Pydantic models** to manage the state and pass structured context between agents, ensuring data consistency across the graph.
*   **MCP Integration & Dynamic Tooling:** Employs the **UnifiedMCPGateway** to dynamically discover and load tools from **MCP Servers** (Brave Search, Fetch, OpenStreetMap, Wikipedia) via standardized adapters. Adding new capabilities is as simple as updating the `MCP_SERVER_REGISTRY`.

### 📚 RAG Pipeline (Retrieval-Augmented Generation)
*   **Hybrid Knowledge Base:** Combines live web search with a specialized **Pinecone** vector database containing verified municipal documents.
*   **Cloud-Native Storage:** Documents are stored in **Azure Blob Storage** and synchronized to Pinecone via a custom ETL pipeline.
*   **Automated Document Processing:** End-to-end RAG lifecycle including PDF parsing (**pypdf**), semantic chunking (**RecursiveCharacterTextSplitter**), and high-performance embedding generation (**OpenAI text-embedding-3-small**).
*   **Expert Prompting:** The Zoning Law agent leverages optimized system instructions to prioritize RAG records over web fallbacks, maintaining a strict "search budget" for performance.

### 💎 Premium User Experience (Chainlit)
*   **Real-time UX Translation:** Employs a specialized `@llm_translator` agent that intercepts raw technical logs and JSON payloads, converting them into concise, emoji-enhanced progress updates for the end-user.
*   **Interactive Interface:** Custom-styled **Chainlit** dashboard with dedicated CSS and theme configurations for a professional investor experience.
*   **Action Tracking:** Every tool usage and node action is streamed directly to the main UI chat window, providing a transparent "audit trail" of the AI's reasoning process.

### 🛡️ Robust Engineering
*   **Type-Safe Contracts:** Utilizes **Pydantic** for structured output and state management, separated into per-agent models in `state.py` for maximum clarity and maintainability.
*   **Reliability Guardrails:** Implements a strict `recursion_limit` for graph execution to prevent infinite agentic loops and ensure system stability.
*   **Token Optimization:** Custom logic in `tools.py` sanitizes tool schemas and injects restrictive defaults to minimize token consumption and API costs.
*   **Deterministic Underwriting:** The Financial Modeler agent uses a sandboxed **Python REPL** to calculate Cap Rates, NOI, and Cash-on-Cash returns with 100% mathematical precision.

---

## 🤖 Agent Architecture & Roles

The complete graph topology and node definitions are maintained in `graph.py`.

1.  **Ingest Agent:** Uses Pydantic for schema-strict extraction of user criteria (City, Budget, Strategy) from natural language.
2.  **Supervisor Agent:** Implements router logic to orchestrate the research phase and fan-out tasks to worker nodes.
3.  **Market Data Agent:** Retrieves live listings and pricing telemetry via asynchronous **RapidAPI** calls.
4.  **Neighborhood Vibe Agent:** Analyzes community sentiment and connectivity using **OpenStreetMap** and **Wikipedia** via MCP.
5.  **Zoning Law Agent:** Executes a RAG workflow to query **Pinecone** for land-use restrictions and STR regulations.
6.  **Financial Modeler Agent:** A high-fidelity "Synthesizer" that executes code-based modeling via **Python REPL** to generate the final prospectus.

---

## 🛠️ Installation & Setup

### Prerequisites
*   Python 3.12+ (managed via **uv**)
*   Docker & Docker Compose

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

### Sync Knowledge Base (RAG)

Required only when updating documents.

```bash
uv run python scripts/sync_knowledge_base.py
```

### Option 1: Running via CLI (Recommended for Development)

#### Run Application
```bash
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
*Access the app at `http://localhost:8080/`.*

---

## 🧪 Testing & Mocking

The application provides a comprehensive mocking suite for local development and CI testing to reduce API spend:
*   **Agent Mocks:** Each agent can be toggled to return pre-configured responses using environment variables:
    *   `MOCK_INGEST_INPUT_AGENT_OUTPUT`
    *   `MOCK_MARKET_DATA_AGENT_OUTPUT`
    *   `MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT`
    *   `MOCK_ZONING_LAW_AGENT_OUTPUT`
    *   `MOCK_FINANCIAL_MODELER_AGENT_OUTPUT`
*   **API Mocks:** `MOCK_MARKET_DATA_API` allows testing market logic without consuming RapidAPI credits.

---

## 📈 Optimization Strategies

### 🎯 Advanced Prompt Engineering
*   **Dynamic "Good Enough" Criteria:** Agents are primed with efficiency-first instructions to minimize LLM latency by stopping research once core criteria are met.
*   **Few-Shot Prospectus Generation:** The Financial Modeler uses curated examples to ensure consistent, professional-grade markdown formatting.
*   **System Message Specialization:** Each agent role is defined by a highly focused system prompt that restricts its scope to its specific domain, reducing hallucinations.

### 💰 Cost Optimization Techniques
*   **Model Tiering:** Leveraging different model tiers (e.g., GPT-4o-mini for routing/extraction and GPT-4o for final synthesis) to balance quality and cost.
*   **Schema Sanitization:** Custom logic removes redundant JSON metadata from tool definitions before sending to the LLM.
*   **Context Pruning:** Automatic truncation of massive tool outputs (e.g., web fetch results) to the most relevant 2000-4000 characters.
*   **RAG Priority:** The Zoning agent is strictly instructed to prioritize local RAG data from Pinecone before falling back to expensive web searches.
*   **Restrictive Tool Defaults:** Hard-coded limits on search results (e.g., max 3 results) and fetch lengths to prevent token-heavy data dumps.

---

## 🔮 Future Roadmap

*   **Semantic Caching:** Implementation of a vector-based cache for common market queries to further reduce API spend.
*   **Infrastructure as Code (IaC):** Implement **Terraform** for reproducible Azure environment setup.
*   **Orchestration:** Migration to **Kubernetes** (AKS) for enterprise-scale auto-scaling.
*   **Observability:** Integration of **LangSmith** or **Arize Phoenix** for deeper trace analysis and evaluation.
*   **Agentic Self-Correction:** Implementing a "Critique" loop where the Supervisor validates agent outputs against the initial user request.
*   **Multi-Model Fallbacks:** Automatically switching to alternative providers (e.g., Anthropic or local models) in case of API outages or rate limits.
