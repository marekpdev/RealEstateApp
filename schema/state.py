from typing import Annotated, List, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from pydantic import BaseModel, Field

from schema.market_data import MarketDataAgentOutput

class IngestInputAgentOutput(BaseModel):
    city: str = Field(..., description="The city extracted from the user's input")
    budget: str = Field(..., description="The maximum budget extracted from the user's input")

class NeighborhoodVibeAgentOutput(BaseModel):
    """
    Flat structured output for the Neighborhood Vibe Agent.
    Forces the LLM to package semantic research into a single string.
    """
    neighborhood_vibe: str = Field(
        ...,
        description="Qualitative community sentiment analysis, sub-neighborhood dynamics, and environmental risk profiles from RAG."
    )

class ZoningLawAgentOutput(BaseModel):
    """
    Flat structured output for the Zoning Law Agent.
    Forces the LLM to package regulatory research into a single string.
    """
    zoning_laws: str = Field(
        ...,
        description="A concise synthesis of key multi-family zoning metrics (height, density, setbacks) and STR regulations identified for the target market. If specific metrics are not found in 1-2 quick searches, explicitly state they are unknown."
    )

class FinancialModelerAgentOutput(BaseModel):
    """
    Flat structured output for the Financial Modeler Agent.
    Forces the LLM to package the final multi-source synthesis into a single string.
    """
    financial_report: str = Field(
        ...,
        description="The final synthesized real estate investment prospectus markdown report combining market listings, neighborhood sentiments, and zoning laws."
    )

class OverallGraphState(BaseModel):
    """ The global state for the Real Estate Investment Planner. """
    messages: Annotated[List[BaseMessage], add_messages] = Field(default_factory=list)
    ingest_input: Optional[IngestInputAgentOutput] = Field(
        None,
        description="Nested model holding basic parameters extracted from the initial user prompt."
    )
    market_data: Optional[MarketDataAgentOutput] = Field(
        None,
        description="Nested model holding extracted property metrics and active listings."
    )
    neighborhood_vibe: Optional[NeighborhoodVibeAgentOutput] = Field(
        None,
        description="Nested model holding semantic community attributes and sentiment scores."
    )
    zoning_laws: Optional[ZoningLawAgentOutput] = Field(
        None,
        description="Nested model holding the synthesized zoning and regulatory research summary."
    )
    financial_report: Optional[FinancialModelerAgentOutput] = Field(
        None,
        description="Nested model holding the ultimate synthesized investment prospectus markdown document."
    )