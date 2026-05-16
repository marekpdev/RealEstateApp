from dotenv import load_dotenv
import os
from langchain_openai import ChatOpenAI

load_dotenv()

key = os.getenv("GITHUB_TOKEN")

if key:
    print(f"Success! Key loaded. (Starts with: {key[:5]})")
else:
    print("Error: API Key not found. Check your .env file location.")

# model = "gpt-4o"
model = "gpt-4o-mini"

base_model = ChatOpenAI(
    model=model,
    api_key=os.getenv("GITHUB_TOKEN"),
    base_url="https://models.inference.ai.azure.com"
)