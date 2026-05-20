from langchain_core.messages import AIMessage

from schema.state import OverallGraphState

def mock_market_data_node(state: OverallGraphState):
    """
    Mock Market Data Agent: Simulates querying a PostgreSQL relational database
    using the extracted state variables.
    """
    # Simulating what a SQL tool would return based on state.city and state.budget
    mock_sql_result = (
        f"SQL Query Result for {state.city} (Max: {state.budget}):\n"
        "- 123 Brickell Ave: $580,000 | 2 Bed | 2 Bath | Cap Rate: 6.2%\n"
        "- 789 Wynwood Way: $520,000 | 1 Bed | 1.5 Bath | Cap Rate: 5.8%\n"
        "Market Trend: Median price per sq ft has expanded 4.5% year-over-year."
    )

    return {
        "market_data": mock_sql_result,
        "messages": [
            AIMessage(
                content="Market Data Agent: Successfully scanned relational tables. Loaded matching listings into state.",
                name="MarketAgent"
            )
        ]
    }