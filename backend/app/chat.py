"""A small, in-memory chat session backed by Groq."""

from groq import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    Groq,
    RateLimitError,
)
from groq.types.chat import ChatCompletionMessageParam


SYSTEM_MESSAGE = (
    "You are Sagar Code Agent, a helpful coding assistant. "
    "Give clear explanations and practical code examples. "
    "You do not have access to the user's files in this stage."
)
MAX_HISTORY_TURNS = 10


class ChatError(Exception):
    """An error that can be shown directly in the terminal."""


class ChatSession:
    """Keep the most recent turns and request replies from Groq."""

    def __init__(self, client: Groq, model: str) -> None:
        self._client = client
        self._model = model
        self._history: list[ChatCompletionMessageParam] = []

    def clear(self) -> None:
        """Forget the conversation history for this session."""
        self._history.clear()

    def reply(self, prompt: str) -> str:
        """Send a prompt with recent history and return the assistant reply."""
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": SYSTEM_MESSAGE},
            *self._history,
            {"role": "user", "content": prompt},
        ]

        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except AuthenticationError as error:
            raise ChatError(
                "Groq rejected the API key. Check GROQ_API_KEY in backend/.env."
            ) from error
        except RateLimitError as error:
            raise ChatError(
                "Groq rate limit reached. Wait a moment, then try again."
            ) from error
        except APIConnectionError as error:
            raise ChatError(
                "Could not connect to Groq. Check your internet connection and retry."
            ) from error
        except APIStatusError as error:
            raise ChatError(
                f"Groq returned HTTP {error.status_code}. Check the configured model "
                "and try again."
            ) from error

        content = completion.choices[0].message.content
        answer = content.strip() if isinstance(content, str) else ""
        if not answer:
            answer = "(Groq returned an empty response.)"

        # Store only successful turns. Keep the system message separate so each
        # request always starts with it and the session stays bounded.
        self._history.extend(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": answer},
            ]
        )
        max_messages = MAX_HISTORY_TURNS * 2
        self._history = self._history[-max_messages:]
        return answer
