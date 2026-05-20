from dotenv import load_dotenv
import os
from langchain_openai import ChatOpenAI
from config import LLMModelType, APIEndpoint

load_dotenv()

key = os.getenv("GITHUB_TOKEN")

if key:
    print(f"Success! Key loaded. (Starts with: {key[:5]})")
else:
    print("Error: API Key not found. Check your .env file location.")

base_model = ChatOpenAI(
    model= LLMModelType.FAST_MODEL.value,
    api_key=key,
    base_url=APIEndpoint.GITHUB_MODELS.value
)