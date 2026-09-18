from langchain_openai import ChatOpenAI
from config import LLMModelType, APIEndpoint
from config.config import GH_TOKEN
from config.config import OPENAI_API_KEY
from config.safety import guarded_httpx_clients

# Using OpenAI through GitHub API I got error
# openai.APIStatusError: Error code: 413 - {'error': {'code': 'tokens_limit_reached', 'message': 'Request body too large for gpt-4o-mini model. Max size: 8000 tokens.',
# 'details': 'Request body too large for gpt-4o-mini model. Max size: 8000 tokens.'}}
# base_model = ChatOpenAI(
#     model= LLMModelType.FAST_MODEL.value,
#     api_key=GH_TOKEN,
#     base_url=APIEndpoint.GITHUB_MODELS.value
# )
# I can use direct OpenAI directly

# ChatOpenAI validates that an api_key string is present at construction time, which
# runs at import time here. A placeholder keeps import working with zero credentials;
# it is never used for a real request because the http_client/http_async_client pair
# below refuses any request while OFFLINE_MODE is on (see config/safety.py).
_http_client, _http_async_client = guarded_httpx_clients("config.llm.base_model")

base_model = ChatOpenAI(
    model= LLMModelType.FAST_MODEL.value,
    api_key= OPENAI_API_KEY or "sk-offline-mode-placeholder",
    http_client=_http_client,
    http_async_client=_http_async_client,
    http_socket_options=(),
)