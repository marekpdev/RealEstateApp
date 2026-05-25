from langchain_openai import ChatOpenAI
from config import LLMModelType, APIEndpoint
from config.config import GITHUB_TOKEN

base_model = ChatOpenAI(
    model= LLMModelType.FAST_MODEL.value,
    api_key=GITHUB_TOKEN,
    base_url=APIEndpoint.GITHUB_MODELS.value
)