import os
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("OPENAI_API_KEY")

if key:
    print(f"✅ Success! Key loaded. (Starts with: {key[:5]})")
else:
    print("❌ Error: API Key not found. Check your .env file location.")