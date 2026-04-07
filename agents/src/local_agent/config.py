import os
from dotenv import load_dotenv

def load_env() -> None:
    load_dotenv()

def get_env(name: str, default: str | None = None) -> str:
    v = os.getenv(name, default)
    if v is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v