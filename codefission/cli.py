"""CLI entry point for CodeFission.

The CLI is a pure HTTP client — it talks to the REST API served by `fission serve`.
The `serve` command launches the server (GUI + API).

CLI state is persisted in ~/.codefission/cli_state.json:
  {"tree_id": "abc123", "node_id": "def456"}
"""

import atexit
import json
import os
import shutil
import socket
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import click

DATA_DIR = Path.home() / ".codefission"
DEFAULT_PORT = 19440
PORT_RANGE = range(19440, 19450)
LOCK_FILE = DATA_DIR / "server.lock"
CLI_STATE_FILE = DATA_DIR / "cli_state.json"


# ── Server discovery ─────────────────────────────────────────────────


def _require_server() -> str:
    """Find the running server. Returns base URL or exits."""
    lock = _read_lock()
    if lock:
        port = lock.get("port", DEFAULT_PORT)
        base = f"http://localhost:{port}"
        try:
            import httpx
            httpx.get(f"{base}/health", timeout=0.5)
            return base
        except Exception:
            pass
    click.echo("Server not running. Start it with: fission serve", err=True)
    raise SystemExit(1)


# ── CLI state ─────────────────────────────────────────────────────────


def _load_state() -> dict:
    if CLI_STATE_FILE.exists():
        try:
            return json.loads(CLI_STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict):
    CLI_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CLI_STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def _current_tree_id() -> str | None:
    return _load_state().get("tree_id")


def _current_node_id() -> str | None:
    return _load_state().get("node_id")


# ── Server infrastructure (from old cli.py) ──────────────────────────


def _check_prerequisites():
    missing = []
    if not shutil.which("git"):
        missing.append(
            "git - install from https://git-scm.com/downloads"
            "\n      macOS: xcode-select --install"
            "\n      Ubuntu/Debian: sudo apt install git"
            "\n      Windows: https://git-scm.com/download/win"
        )
    if not shutil.which("claude"):
        missing.append(
            "Claude Code CLI - install with: npm install -g @anthropic-ai/claude-code"
            "\n      Then authenticate: claude login"
        )
    if missing:
        click.echo("CodeFission requires the following:\n")
        for m in missing:
            click.echo(f"  * {m}\n")
        sys.exit(1)


def _is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _find_available_port(preferred: int) -> int | None:
    if _is_port_available(preferred):
        return preferred
    for port in PORT_RANGE:
        if port != preferred and _is_port_available(port):
            return port
    return None


def _detect_git_root(path: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path), capture_output=True, text=True,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    return None


def _auto_init_repo(path: Path):
    click.echo(f"Initializing git in {path} ...")
    subprocess.run(["git", "init"], cwd=str(path), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(path), check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit", "--allow-empty"],
        cwd=str(path), check=True,
        env={
            **os.environ,
            "GIT_COMMITTER_NAME": "CodeFission",
            "GIT_COMMITTER_EMAIL": "codefission@local",
            "GIT_AUTHOR_NAME": "CodeFission",
            "GIT_AUTHOR_EMAIL": "codefission@local",
        },
    )


def _ensure_gitignore(project_path: Path):
    gitignore = project_path / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        if ".codefission/" not in content:
            with open(gitignore, "a") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write(".codefission/\n")
    else:
        gitignore.write_text(".codefission/\n")


def _compute_repo_id(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=str(repo_path), capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to compute repo_id: {result.stderr}")
    return result.stdout.strip().splitlines()[0]


def _get_head_commit(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_path), capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get HEAD: {result.stderr}")
    return result.stdout.strip()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _read_lock() -> dict | None:
    if not LOCK_FILE.exists():
        return None
    try:
        data = json.loads(LOCK_FILE.read_text())
        pid = data.get("pid")
        if pid and _pid_alive(pid):
            return data
    except Exception:
        pass
    return None


def _acquire_lock(port: int, repo_path: Path | None = None,
                  repo_id: str | None = None, head_commit: str | None = None):
    existing = _read_lock()
    if existing:
        existing_port = existing.get("port", "?")
        url = f"http://localhost:{existing_port}"
        if repo_id and head_commit and repo_path:
            url += f"?repo_id={repo_id}&head={head_commit}&path={quote(str(repo_path), safe='/')}"
        click.echo(f"CodeFission is already running at http://localhost:{existing_port}")
        webbrowser.open(url)
        sys.exit(0)

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(json.dumps({
        "pid": os.getpid(),
        "port": port,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }) + "\n")
    atexit.register(_release_lock)


def _release_lock():
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ── Click CLI ─────────────────────────────────────────────────────────


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """CodeFission -- tree-structured AI development."""
    if ctx.invoked_subcommand is None:
        # Default: show status
        lock = _read_lock()
        if lock:
            port = lock.get("port", "?")
            click.echo(f"Server running at http://localhost:{port}")
        else:
            click.echo("Server not running. Start with: fission serve")

        state = _load_state()
        if state.get("tree_id"):
            click.echo(f"Active tree: {state['tree_id']}")
        if state.get("node_id"):
            click.echo(f"Active node: {state['node_id']}")


# ── serve ─────────────────────────────────────────────────────────────


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--port", default=DEFAULT_PORT, type=int, help="Server port")
def serve(path, port):
    """Launch the UI server."""
    import uvicorn

    _check_prerequisites()

    target_path = Path(path).resolve()

    if not target_path.is_dir():
        click.echo(f"Error: {target_path} is not a directory.", err=True)
        raise SystemExit(1)

    repo_path = None
    repo_id = None
    head_commit = None
    is_home = target_path == Path.home()

    if not is_home:
        git_root = _detect_git_root(target_path)
        if git_root:
            repo_path = git_root
        else:
            if sys.stdin.isatty():
                if not click.confirm("This directory is not a git repo. Initialize one?", default=True):
                    raise SystemExit(0)
            else:
                click.echo("Error: Not a git repo and not running interactively.", err=True)
                raise SystemExit(1)
            _auto_init_repo(target_path)
            repo_path = target_path

        repo_id = _compute_repo_id(repo_path)
        head_commit = _get_head_commit(repo_path)
        _ensure_gitignore(repo_path)

    actual_port = _find_available_port(port)
    if actual_port is None:
        click.echo(f"Error: No available port in range {PORT_RANGE.start}-{PORT_RANGE.stop - 1}.", err=True)
        raise SystemExit(1)

    _acquire_lock(actual_port, repo_path, repo_id, head_commit)

    if repo_path:
        os.environ["CODEFISSION_REPO_PATH"] = str(repo_path)
        os.environ["CODEFISSION_REPO_ID"] = repo_id
        os.environ["CODEFISSION_HEAD_COMMIT"] = head_commit
    os.environ["CODEFISSION_PORT"] = str(actual_port)

    if repo_path:
        click.echo(f"Repo:    {repo_path}")
    else:
        click.echo("No repo context (home directory mode)")
    click.echo(f"Server:  http://localhost:{actual_port}")

    uvicorn.run(
        "codefission.main:app",
        host="0.0.0.0",
        port=actual_port,
        ws_ping_interval=30,
        ws_ping_timeout=10,
    )


# ── tree commands ─────────────────────────────────────────────────────


@cli.group()
def tree():
    """Manage trees."""
    pass


@tree.command("ls")
def tree_ls():
    """List all trees."""
    import httpx

    base = _require_server()
    r = httpx.get(f"{base}/api/trees", timeout=5)
    r.raise_for_status()
    trees = r.json().get("trees", [])

    if not trees:
        click.echo("No trees.")
        return

    state = _load_state()
    active = state.get("tree_id")
    for t in trees:
        marker = " *" if t["id"] == active else ""
        click.echo(f"  {t['id']}  {t['name']}{marker}")


@tree.command("new")
@click.argument("name")
@click.option("--branch", default="main", help="Base branch")
@click.option("--from", "from_node", default=None, help="Fork from this node ID")
def tree_new(name, branch, from_node):
    """Create a new tree."""
    import httpx

    base = _require_server()
    body = {"name": name, "base_branch": branch}

    r = httpx.post(f"{base}/api/trees", json=body, timeout=10)
    r.raise_for_status()
    data = r.json()
    tree_id = data["tree"]["id"]

    # Auto-select the new tree
    state = _load_state()
    state["tree_id"] = tree_id
    root_id = data.get("root", {}).get("id")
    if root_id:
        state["node_id"] = root_id
    _save_state(state)

    click.echo(f"Created tree {tree_id}: {name}")


@tree.command("rm")
@click.argument("tree_id")
def tree_rm(tree_id):
    """Delete a tree."""
    import httpx

    base = _require_server()
    r = httpx.delete(f"{base}/api/trees/{tree_id}", timeout=5)
    r.raise_for_status()

    state = _load_state()
    if state.get("tree_id") == tree_id:
        state.pop("tree_id", None)
        state.pop("node_id", None)
        _save_state(state)

    click.echo(f"Deleted tree {tree_id}")


@tree.command("use")
@click.argument("tree_id")
def tree_use(tree_id):
    """Switch to a tree (set as active)."""
    import httpx

    base = _require_server()
    # Verify it exists
    r = httpx.get(f"{base}/api/trees", timeout=5)
    r.raise_for_status()
    trees = r.json().get("trees", [])
    match = [t for t in trees if t["id"] == tree_id or t["id"].startswith(tree_id)]
    if not match:
        click.echo(f"Tree {tree_id} not found.", err=True)
        raise SystemExit(1)

    resolved = match[0]["id"]
    state = _load_state()
    state["tree_id"] = resolved
    state.pop("node_id", None)
    _save_state(state)
    click.echo(f"Switched to tree {resolved}: {match[0]['name']}")


# ── node commands ─────────────────────────────────────────────────────


@cli.command("ls")
def node_ls():
    """List nodes in the active tree."""
    import httpx

    base = _require_server()
    tree_id = _current_tree_id()
    if not tree_id:
        click.echo("No active tree. Use: fission tree use <id>", err=True)
        raise SystemExit(1)

    # Fetch all nodes by getting the tree (which includes root_node_id)
    r = httpx.get(f"{base}/api/trees", timeout=5)
    r.raise_for_status()
    trees = r.json().get("trees", [])
    tree = next((t for t in trees if t["id"] == tree_id), None)
    if not tree:
        click.echo(f"Tree {tree_id} not found.", err=True)
        raise SystemExit(1)

    # For now, display the tree name and ID
    click.echo(f"Tree: {tree['name']} ({tree_id})")
    click.echo(f"  (Use 'fission show <node_id>' to inspect a node)")


@cli.command("select")
@click.argument("node_id")
def select_node(node_id):
    """Set the active node."""
    state = _load_state()
    state["node_id"] = node_id
    _save_state(state)
    click.echo(f"Selected node {node_id}")


@cli.command("show")
@click.argument("node_id", required=False)
def show_node(node_id):
    """Show a node's conversation."""
    import httpx

    base = _require_server()
    tree_id = _current_tree_id()
    if not tree_id:
        click.echo("No active tree.", err=True)
        raise SystemExit(1)

    node_id = node_id or _current_node_id()
    if not node_id:
        click.echo("No node specified. Use: fission show <node_id>", err=True)
        raise SystemExit(1)

    r = httpx.get(f"{base}/api/trees/{tree_id}/nodes/{node_id}", timeout=5)
    if r.status_code == 404:
        click.echo(f"Node {node_id} not found.", err=True)
        raise SystemExit(1)
    r.raise_for_status()
    node = r.json()["node"]

    click.echo(f"Node: {node['id']}")
    click.echo(f"Status: {node['status']}")
    if node.get("provider"):
        click.echo(f"Provider: {node['provider']}")
    if node.get("model"):
        click.echo(f"Model: {node['model']}")
    click.echo()
    if node.get("user_message"):
        click.echo(click.style("User:", bold=True))
        click.echo(node["user_message"])
        click.echo()
    if node.get("assistant_response"):
        click.echo(click.style("Assistant:", bold=True))
        click.echo(node["assistant_response"])


@cli.command("branch")
@click.argument("label", default="")
def branch_cmd(label):
    """Create a branch from the active node."""
    import httpx

    base = _require_server()
    tree_id = _current_tree_id()
    node_id = _current_node_id()
    if not tree_id or not node_id:
        click.echo("No active tree/node.", err=True)
        raise SystemExit(1)

    r = httpx.post(
        f"{base}/api/trees/{tree_id}/nodes/{node_id}/branch",
        json={"label": label},
        timeout=5,
    )
    r.raise_for_status()
    child = r.json()["node"]

    state = _load_state()
    state["node_id"] = child["id"]
    _save_state(state)
    click.echo(f"Created branch {child['id']}")


@cli.command("rm")
@click.argument("node_id")
def rm_node(node_id):
    """Delete a node and its subtree."""
    import httpx

    base = _require_server()
    tree_id = _current_tree_id()
    if not tree_id:
        click.echo("No active tree.", err=True)
        raise SystemExit(1)

    r = httpx.delete(f"{base}/api/trees/{tree_id}/nodes/{node_id}", timeout=5)
    if r.status_code == 400:
        click.echo(f"Error: {r.json().get('detail', 'Unknown error')}", err=True)
        raise SystemExit(1)
    r.raise_for_status()
    data = r.json()
    click.echo(f"Deleted {len(data.get('deleted_ids', []))} node(s)")


# ── chat command ──────────────────────────────────────────────────────


@cli.command()
@click.argument("message")
@click.option("-q", "--quote", "quote_files", multiple=True, help="File to quote as context")
@click.option("--model", default=None, help="Model override")
def chat(message, quote_files, model):
    """Send a message and stream the response."""
    import httpx

    base = _require_server()
    tree_id = _current_tree_id()
    node_id = _current_node_id()
    if not tree_id or not node_id:
        click.echo("No active tree/node. Create one first.", err=True)
        raise SystemExit(1)

    body = {"message": message}
    if quote_files:
        body["file_quotes"] = [
            {"node_id": node_id, "type": "file", "path": f}
            for f in quote_files
        ]

    # SSE streaming
    with httpx.stream(
        "POST",
        f"{base}/api/trees/{tree_id}/nodes/{node_id}/chat",
        json=body,
        timeout=300,
    ) as response:
        if response.status_code != 200:
            click.echo(f"Error: {response.status_code}", err=True)
            raise SystemExit(1)

        new_node_id = None
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            data = json.loads(line[6:])
            event_type = data.get("type")

            if event_type == "node_created":
                new_node_id = data["node"]["id"]

            elif event_type == "text_delta":
                click.echo(data["text"], nl=False)

            elif event_type == "tool_start":
                name = data.get("name", "")
                click.echo(f"\n[tool: {name}]", nl=False)

            elif event_type == "tool_end":
                name = data.get("name", "")
                status = "ERROR" if data.get("is_error") else "ok"
                click.echo(f"\n[/{name}: {status}]", nl=False)

            elif event_type == "done":
                click.echo()  # final newline
                git_commit = data.get("git_commit")
                files_changed = data.get("files_changed", 0)
                if git_commit:
                    click.echo(f"[commit: {git_commit[:12]}, files: {files_changed}]")

            elif event_type == "error":
                click.echo(f"\nError: {data.get('error', 'Unknown')}", err=True)

        # Update state to point at the new node
        if new_node_id:
            state = _load_state()
            state["node_id"] = new_node_id
            _save_state(state)


# ── files / diff ──────────────────────────────────────────────────────


@cli.command("files")
@click.argument("node_id", required=False)
def files_cmd(node_id):
    """List files for a node."""
    import httpx

    base = _require_server()
    tree_id = _current_tree_id()
    node_id = node_id or _current_node_id()
    if not tree_id or not node_id:
        click.echo("No active tree/node.", err=True)
        raise SystemExit(1)

    r = httpx.get(f"{base}/api/trees/{tree_id}/nodes/{node_id}/files", timeout=5)
    r.raise_for_status()
    files = r.json().get("files", [])
    for f in files:
        click.echo(f"  {f}")


@cli.command("diff")
@click.argument("node_id", required=False)
def diff_cmd(node_id):
    """Show diff for a node."""
    import httpx

    base = _require_server()
    tree_id = _current_tree_id()
    node_id = node_id or _current_node_id()
    if not tree_id or not node_id:
        click.echo("No active tree/node.", err=True)
        raise SystemExit(1)

    r = httpx.get(f"{base}/api/trees/{tree_id}/nodes/{node_id}/diff", timeout=5)
    r.raise_for_status()
    diff = r.json().get("diff", "")
    if diff:
        click.echo(diff)
    else:
        click.echo("No changes.")


# ── settings ──────────────────────────────────────────────────────────


@cli.command("set")
@click.argument("key", required=False)
@click.argument("value", required=False)
@click.option("--tree", "for_tree", is_flag=True, help="Set on active tree instead of global")
@click.option("--reset", is_flag=True, help="Reset a setting to default")
def settings_cmd(key, value, for_tree, reset):
    """View or update settings."""
    import httpx

    base = _require_server()

    if not key:
        # Show all settings
        r = httpx.get(f"{base}/api/settings", timeout=5)
        r.raise_for_status()
        data = r.json()
        click.echo("Global defaults:")
        for k, v in data.get("global_defaults", {}).items():
            click.echo(f"  {k}: {v}")
        return

    if reset:
        value = None

    if for_tree:
        tree_id = _current_tree_id()
        if not tree_id:
            click.echo("No active tree.", err=True)
            raise SystemExit(1)
        r = httpx.patch(
            f"{base}/api/trees/{tree_id}",
            json={key: value},
            timeout=5,
        )
    else:
        # Map short names to setting keys
        key_map = {
            "provider": "default_provider",
            "model": "default_model",
            "max_turns": "default_max_turns",
        }
        setting_key = key_map.get(key, key)
        r = httpx.patch(
            f"{base}/api/settings",
            json={setting_key: value},
            timeout=5,
        )

    r.raise_for_status()
    click.echo("Updated.")


# ── log ───────────────────────────────────────────────────────────────


@cli.command("log")
@click.option("--json-output", "--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--limit", default=50, help="Number of actions to show")
def log_cmd(as_json, limit):
    """Show audit log for the active tree."""
    import httpx

    base = _require_server()
    tree_id = _current_tree_id()
    if not tree_id:
        click.echo("No active tree.", err=True)
        raise SystemExit(1)

    r = httpx.get(f"{base}/api/trees/{tree_id}/log", params={"limit": limit}, timeout=5)
    r.raise_for_status()
    actions = r.json().get("actions", [])

    if as_json:
        click.echo(json.dumps(actions, indent=2))
        return

    if not actions:
        click.echo("No actions recorded.")
        return

    for a in actions:
        ts = a["ts"][:19].replace("T", " ")
        click.echo(f"  {a['seq']:>4}  {ts}  {a['kind']:<20} {a.get('source', 'gui')}")


# ── providers ─────────────────────────────────────────────────────────


@cli.command("providers")
def providers_cmd():
    """List available providers."""
    import httpx

    base = _require_server()
    r = httpx.get(f"{base}/api/providers", timeout=5)
    r.raise_for_status()
    providers = r.json().get("providers", [])

    for p in providers:
        click.echo(f"  {p['id']:<15} {p['name']}")
        click.echo(f"    models: {', '.join(p['models'])}")
        click.echo(f"    auth:   {', '.join(p['auth_modes'])}")


# ── Entry point ───────────────────────────────────────────────────────


def main():
    """Entry point for the `fission` CLI."""
    cli()


if __name__ == "__main__":
    main()
