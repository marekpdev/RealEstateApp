from langchain_openai import ChatOpenAI

import config.config
from config import LLMModelType, APIEndpoint
from config.config import GH_TOKEN

# Using OpenAI through GitHub API I got error
# openai.APIStatusError: Error code: 413 - {'error': {'code': 'tokens_limit_reached', 'message': 'Request body too large for gpt-4o-mini model. Max size: 8000 tokens.',
# 'details': 'Request body too large for gpt-4o-mini model. Max size: 8000 tokens.'}}
base_model = ChatOpenAI(
    model= LLMModelType.FAST_MODEL.value,
    api_key=GH_TOKEN,
    base_url=APIEndpoint.GITHUB_MODELS.value
)

# I can use direct OpenAI directly but
# base_model = ChatOpenAI(
#     model= LLMModelType.FAST_MODEL.value,
#     api_key= config.config.OPENAI_API_KEY
# )