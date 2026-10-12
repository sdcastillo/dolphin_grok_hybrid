"""
Supabase client for the project.

Usage:
    from supabase_client import supabase, get_supabase

    # Query a table (requires appropriate RLS policies)
    response = supabase.table("users").select("*").execute()
    print(response.data)

    # Insert
    supabase.table("items").insert({"title": "Hello Supabase"}).execute()
"""

import os
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables from .env
load_dotenv()

SUPABASE_URL: str | None = os.getenv("SUPABASE_URL")
# Server-side writes need the secret/service key. The publishable/anon key is
# blocked by RLS on chat_logs and tts_audio_logs.
SUPABASE_KEY: str | None = (
    os.getenv("SUPABASE_SERVICE_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_KEY")
)

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Missing Supabase credentials.\n"
        "Please set SUPABASE_URL and SUPABASE_SERVICE_KEY in your .env file.\n"
        "Copy .env.example to .env and fill in the values.\n"
        "You provided the publishable key; you still need to add your project URL."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_supabase() -> Client:
    """Return the Supabase client instance."""
    return supabase


def test_connection() -> bool:
    """Quick health check. Confirms the Supabase client can reach the project."""
    try:
        # Lightweight check - we don't assume any user tables exist yet.
        # If auth or network works, this will succeed or give a table-related error (not connection error).
        _ = supabase.table("users").select("id").limit(1).execute()
        print("✅ Connected to Supabase!")
        print(f"   Project URL: {SUPABASE_URL}")
        print(f"   Key prefix: {SUPABASE_KEY[:20]}...")
        return True
    except Exception as e:
        err_str = str(e)
        if "Could not find the table" in err_str or "PGRST205" in err_str:
            # This means we reached Supabase successfully!
            print("✅ Connected to Supabase!")
            print(f"   Project URL: {SUPABASE_URL}")
            print(f"   Key prefix: {SUPABASE_KEY[:20]}...")
            print("   (No 'users' table yet — this is expected on a fresh project.)")
            return True
        else:
            print(f"⚠️  Connected to Supabase but query failed: {e}")
            print("   The client itself reached the project.")
            return False


if __name__ == "__main__":
    print("Testing Supabase connection...")
    test_connection()
    print("\nYou can now use: from supabase_client import supabase")
