# 🏢 Real Estate AI Investment Planner

Welcome! This system is an advanced multi-agent orchestrator built with **LangGraph** and powered by structured LLM parsing. It is designed to evaluate real estate deals by combining disparate data layers in parallel.

### 🧠 How It Works Under the Hood
When you request a market deal calculation, the system initializes a state graph that handles the following execution path:

1. **Ingest Gateway**: Extracts parameters (location, budget bounds) into rigorous Pydantic schemas.
2. **Supervisor Router**: Spawns multiple parallel workers simultaneously.
3. **Data Analysis Workers (Parallel Execution)**:
   - **Market Data Agent**: Queries active MLS listings and calculates baseline cap rates.
   - **Neighborhood Vibe Agent**: Conducts semantic searches across vector databases for community logs and sentiment.
   - **Zoning Law Agent**: Parses municipal codes, density boundaries, and short-term rental (STR) constraints.
4. **Financial Modeler**: Synthesizes all worker buckets to generate a final markdown prospectus investment report.

### 🚀 Try It Out
Type an investment scenario into the chat bar below to watch the underlying LangGraph nodes activate in real time.

**Example Prompts to Paste:**
* *"I would like to invest in Los Angeles, CA, and my max budget is $800,000. Calculate deal metrics."*
* *"Crunch investment metrics for a property in Hollywood Hills under $800,000."*