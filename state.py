from typing import Annotated, List, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from pydantic import BaseModel, Field

class OverallGraphState(BaseModel):
    """ The global state for the Real Estate Investment Planner. """
    messages: Annotated[List[BaseMessage], add_messages] = Field(default_factory=list)
    # Structural parameters parsed by the Ingest Node
    city: Optional[str] = Field(None, description="The targeted real estate market.")
    budget: Optional[str] = Field(None, description="The maximum financial investment ceiling.")

    # Storage buckets for your specialized researchers
    market_data: Optional[str] = Field(None, description="Raw real estate pricing & listing metrics from SQL.")
    neighborhood_vibe: Optional[str] = Field(None, description="Qualitative community sentiment analysis from RAG.")
