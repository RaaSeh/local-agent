from dotenv import load_dotenv
load_dotenv()

from local_agent.integrations.telegram_bot import run_telegram_bot


if __name__ == "__main__":
    raise SystemExit(run_telegram_bot())
