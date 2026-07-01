"""
Example: Using OpenAI with history loaded from our Supabase chat_logs.

Key reality check:
- OpenAI does NOT provide an API to "load all previous chats".
- The chat.completions.list() usually returns nothing or very little.
- Assistants threads can be retrieved only if you store the thread_id yourself.

The correct pattern (used here):
1. Load previous turns from YOUR database (Supabase chat_logs in this project).
2. Format them as OpenAI messages.
3. Send to OpenAI.
4. Save the new response back to your database.

Usage:
    python examples/openai_chat_history.py "Tell me more about the Second Amendment"
"""

import os
import sys
from dotenv import load_dotenv
from openai import OpenAI

# Project imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from direct_db import execute

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def load_recent_history(limit: int = 10) -> list[dict]:
    """
    Load recent chat logs from Supabase and convert to OpenAI message format.
    We treat prompt = user, response = assistant.
    """
    rows = execute(
        """
        SELECT prompt, response, created_at 
        FROM chat_logs 
        WHERE status = 'success' 
          AND prompt IS NOT NULL 
          AND response IS NOT NULL
        ORDER BY created_at DESC 
        LIMIT %s
        """,
        (limit,)
    )
    
    messages = []
    for row in reversed(rows):  # oldest first for proper order
        if row["prompt"]:
            messages.append({"role": "user", "content": row["prompt"]})
        if row["response"]:
            messages.append({"role": "assistant", "content": row["response"]})
    
    return messages


def chat_with_openai(user_message: str, model: str = "gpt-4o-mini", max_history: int = 8):
    """Load local history + call OpenAI."""
    history = load_recent_history(limit=max_history)
    
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Use the provided history for context."}
    ] + history + [
        {"role": "user", "content": user_message}
    ]
    
    print(f"Loaded {len(history)} messages from local chat history.")
    print(f"Sending to OpenAI ({model})...")
    
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=800,
        temperature=0.7,
    )
    
    reply = response.choices[0].message.content
    print("\n=== OpenAI Reply ===\n")
    print(reply)
    print("\n====================\n")
    
    # You could now log this back to Supabase using supabase_logging.log_chat(...)
    print("Tip: Call log_chat() from supabase_logging to save this turn.")
    
    return reply


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python examples/openai_chat_history.py \"your question here\"")
        sys.exit(1)
    
    user_q = " ".join(sys.argv[1:])
    chat_with_openai(user_q)
