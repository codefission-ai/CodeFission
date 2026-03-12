# Contributing to CodeFission

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
# Clone and set up the backend
git clone https://github.com/your-username/codefission.git
cd codefission
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Set up the frontend (with hot reload)
cd frontend
npm install
npm run dev     # Starts Vite dev server on :5173
```

Run the backend separately during development:

```bash
source venv/bin/activate
cd backend
uvicorn main:app --reload --port 8080
```

## Project Structure

```
├── backend/          # FastAPI application
│   ├── main.py       # App entry point and routes
│   ├── db.py         # Database connection and migrations
│   ├── models.py     # Pydantic models
│   ├── tree_service.py
│   ├── chat_service.py
│   └── workspace_service.py
├── frontend/         # React + TypeScript (Vite)
│   └── src/
│       ├── App.tsx
│       ├── store.ts  # State management
│       └── components/
├── data/             # SQLite DB + git workspaces (gitignored)
└── run.sh            # Production start script
```

## Coding Standards

- **Python:** Follow PEP 8. Use type hints where practical.
- **TypeScript:** Use strict mode. Prefer functional components with hooks.
- **Commits:** Write concise, descriptive commit messages.

## Pull Request Workflow

1. Fork the repo and create a feature branch from `main`.
2. Make your changes and test them locally.
3. Ensure the frontend builds cleanly: `cd frontend && npm run build`
4. Open a pull request with a clear description of what you changed and why.

## Reporting Issues

Open an issue on GitHub with:
- Steps to reproduce
- Expected vs. actual behavior
- Browser/OS/Python version if relevant
