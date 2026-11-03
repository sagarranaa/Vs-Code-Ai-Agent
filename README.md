# Sagar Code Agent

A local VS Code coding assistant backed by a Python agent and Groq. It can inspect a workspace, explain code, propose edits for review, and suggest validation commands that run only after you approve them in VS Code. A simple terminal chat is also included.

## Features

- Explore project files and search code from a VS Code chat panel.
- Include the active file, selected text, and unsaved editor contents as context.
- Review proposed edits in a VS Code diff and apply them after confirmation.
- Approve suggested validation commands before they run.
- Stream responses and stop an in-progress chat.
- Run a standalone terminal chat without workspace access.

## Requirements

- VS Code 1.92 or later
- Python 3.10 or later
- Node.js and npm
- A [Groq API key](https://console.groq.com/keys)

## Configure the Python backend

From the project root, create a virtual environment and install the backend dependencies:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

On Windows, activate the environment with `.venv\Scripts\Activate.ps1` instead of the `source` command.

Edit `backend/.env` and add your Groq API key:

```dotenv
GROQ_API_KEY=your-groq-api-key
GROQ_MODEL=openai/gpt-oss-20b
```

`GROQ_MODEL` can be changed to another model supported by your Groq account. Keep your key in `backend/.env`; `.env` files are excluded from Git.

## Run the terminal chat

With the backend virtual environment active, run this from `backend/`:

```bash
python -m app.main
```

Use `/clear` to clear the in-memory conversation or `/exit` to quit. The terminal chat keeps up to 10 recent turns and does not read project files. To check that configuration is present without contacting Groq:

```bash
python -m app.main --check-config
```

## Run the VS Code extension

From the project root, install the development dependencies and compile the extension:

```bash
npm install
npm run compile
```

Open the project folder in VS Code, press **F5** to launch the Extension Development Host, then run **Sagar Code Agent: Open Chat** from the Command Palette. The Python backend starts on the first chat request.

By default, the extension uses `backend/.venv/bin/python` on macOS/Linux or `backend/.venv/Scripts/python.exe` on Windows. You can change these settings in VS Code:

| Setting | Purpose |
| --- | --- |
| `sagarCodeAgent.pythonPath` | Python executable used to run the backend. Blank uses the project environment when available, then `python3` or `python`. |
| `sagarCodeAgent.backendDirectory` | Optional path to a backend folder. Blank uses the backend packaged with the extension. |

The chat history is held in the webview session. The panel shows the editor context and workspace tool results sent to Groq.

## Build and install a VSIX

After installing the npm development dependencies, create a VS Code extension package:

```bash
npm run package
```

This creates a `.vsix` file in the project root. In VS Code, run **Extensions: Install from VSIX...** and select the generated file. The package includes the Python source but excludes `.env` files, virtual environments, bytecode, and Node dependencies. For an installed VSIX, configure `sagarCodeAgent.backendDirectory` to point to a backend with its Python dependencies installed, and set `sagarCodeAgent.pythonPath` to that environment's Python executable.

## How it works

1. The VS Code panel sends your prompt and relevant editor context to the extension host.
2. The extension host starts the Python backend on `127.0.0.1` using a temporary port and token. The Groq API key stays in Python and is not sent to the webview.
3. The Python agent sends requests to Groq and handles its workspace tools: list files, read files, search code, propose edits, and suggest commands.
4. The agent's file tools stay within the selected workspace, skip common dependency and build folders, and filter common secret files and detectable credential-like text.
5. Proposed edits appear in a VS Code diff. Applying an edit requires confirmation and a check that the file has not changed since it was read.
6. Suggested commands remain inactive until you approve them in VS Code. They run with shell expansion disabled, a timeout, and an output limit.

Prompts, editor context, workspace results, and approved command output may be sent to Groq as part of a request. Review the context shown in the chat panel before sharing sensitive code. The project is designed for local use and is not a hosted multi-user service.

## Project structure

```text
src/
  extension.ts       VS Code extension, chat panel, and editor integration
backend/app/
  agent.py           Groq tool-calling loop and edit proposals
  workspace.py       Workspace file access and search safeguards
  api.py             Local API used by the extension
  commands.py        Runs commands approved in VS Code
```

## License

No license file is currently included. Add a `LICENSE` file before publishing if you want to grant others explicit reuse rights.
