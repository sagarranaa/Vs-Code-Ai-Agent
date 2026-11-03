import argparse

from groq import Groq
from pydantic import ValidationError

from app.chat import ChatError, ChatSession
from app.config import Settings, get_settings


def _load_settings() -> Settings:
    try:
        settings = get_settings()
    except ValidationError:
        raise SystemExit(
            "Groq API key is missing. Set GROQ_API_KEY in backend/.env."
        ) from None

    api_key = settings.groq_api_key.get_secret_value().strip()
    if not api_key:
        raise SystemExit(
            "Groq API key is empty. Set GROQ_API_KEY in backend/.env."
        )

    return settings


def run_chat(settings: Settings) -> None:
    api_key = settings.groq_api_key.get_secret_value().strip()
    client = Groq(api_key=api_key)
    session = ChatSession(client=client, model=settings.groq_model)

    print("Sagar Code Agent — Groq terminal chat")
    print(f"Model: {settings.groq_model}")
    print("Type /clear to start a fresh conversation or /exit to quit.")

    try:
        while True:
            try:
                prompt = input("\nYou: ").strip()
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print("\nGoodbye.")
                break

            if not prompt:
                continue
            if prompt.lower() in {"/exit", "/quit", "exit", "quit"}:
                print("Goodbye.")
                break
            if prompt.lower() == "/clear":
                session.clear()
                print("Conversation history cleared.")
                continue

            try:
                answer = session.reply(prompt)
            except ChatError as error:
                print(f"\nError: {error}")
                continue

            print(f"\nSagar Code Agent: {answer}")
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with Sagar Code Agent.")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="check local configuration without contacting Groq",
    )
    args = parser.parse_args()

    settings = _load_settings()
    if args.check_config:
        print("Configuration loaded. Groq API key is present.")
        print(f"Configured model: {settings.groq_model}")
        return

    run_chat(settings)


if __name__ == "__main__":
    main()
