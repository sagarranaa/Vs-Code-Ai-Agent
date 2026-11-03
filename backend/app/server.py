"""Run the extension's loopback-only FastAPI service."""

import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("SAGAR_AGENT_PORT", "8765"))
    uvicorn.run(
        "app.api:app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
