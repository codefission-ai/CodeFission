# RepoEvolve

A tree-structured AI conversation tool for exploring code repositories. Branch conversations at any point to explore alternative approaches, compare AI responses, and evolve your understanding of a codebase.

<!-- ![Screenshot](docs/screenshot.png) -->

## Features

- **Tree-structured conversations** — branch any AI response to explore alternatives
- **Repository-aware** — attach git repos to conversations for code-aware AI assistance
- **Multi-provider** — supports Anthropic (Claude) and OpenAI models
- **Live streaming** — real-time token streaming with WebSocket updates
- **Git worktrees** — each conversation branch gets its own isolated worktree
- **File browsing** — view files and diffs for any node in the tree

## Prerequisites

- Python 3.12+
- Node.js 18+
- An API key for at least one provider:
  - `ANTHROPIC_API_KEY` for Claude models
  - `OPENAI_API_KEY` for OpenAI models

## Getting Started

```bash
# Clone the repository
git clone https://github.com/your-username/repoevolve.git
cd repoevolve

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Install frontend dependencies
cd frontend
npm install
npm run build
cd ..

# Set API keys
export ANTHROPIC_API_KEY="sk-ant-..."
# and/or
export OPENAI_API_KEY="sk-..."

# Run the server
./run.sh
```

Open http://localhost:8080 in your browser.

## Configuration

All configuration is done via environment variables:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | API key for Claude models |
| `OPENAI_API_KEY` | API key for OpenAI models |

The server port can be changed by passing it as an argument: `./run.sh 3000`

## Tech Stack

- **Backend:** Python, FastAPI, aiosqlite, WebSockets
- **Frontend:** React, TypeScript, React Flow, Vite
- **AI:** Anthropic Claude SDK, OpenAI SDK, Claude Agent SDK

## License

[MIT](LICENSE)
