# Professional Software Development Pipeline

When people say "dev pipeline" or "CI/CD pipeline," they're talking about the
automated steps that code goes through from the moment a developer writes it to
the moment it's running in production for real users.

Think of it like an assembly line in a factory: raw materials (your code) go
through a series of stations (automated checks and deployments) before the
finished product (working software) reaches the customer.

---

## The Big Picture

```
You write code
    |
    v
Push to GitHub (or GitLab, etc.)
    |
    v
CI runs automatically  <-- "Continuous Integration"
  - Linting (style checks)
  - Tests (unit, integration)
  - Build (compile, bundle)
    |
    v
CD runs automatically  <-- "Continuous Deployment/Delivery"
  - Deploy to staging
  - Deploy to production
```

---

## 1. Version Control (Git + GitHub)

Everything starts here. You work on a branch, push it, and open a **pull
request** (PR). This is a proposal: "here are my changes, please review and
merge them into the main codebase."

**Key concepts:**
- **branch** — an isolated copy of the code where you make changes
- **pull request (PR)** — a request to merge your branch into `main`
- **code review** — teammates read your PR and leave comments before approving
- **merge** — your branch gets folded into `main`

---

## 2. CI — Continuous Integration

CI is the automated robot that checks your code every time you push. It answers:
"did this change break anything?"

A CI system (GitHub Actions, CircleCI, Jenkins, etc.) runs a **pipeline** — a
sequence of jobs defined in a config file (e.g., `.github/workflows/ci.yml`).

### Typical CI jobs:

| Job | What it does | Example tool |
|-----|-------------|--------------|
| **Lint** | Checks code style and catches common mistakes | `ruff`, `eslint`, `flake8` |
| **Type check** | Validates type annotations | `mypy`, `pyright`, `tsc` |
| **Unit tests** | Runs fast, isolated tests | `pytest`, `jest` |
| **Integration tests** | Tests components working together (e.g., with a real DB) | `pytest` with fixtures |
| **Build** | Compiles/bundles the code | `vite build`, `go build`, `docker build` |
| **Security scan** | Checks dependencies for known vulnerabilities | `dependabot`, `snyk` |

### Example: GitHub Actions config

```yaml
# .github/workflows/ci.yml
name: CI
on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: ruff check .           # lint
      - run: pytest                  # tests
```

Every time someone pushes or opens a PR, GitHub spins up a fresh virtual
machine, checks out the code, installs dependencies, and runs those commands.
If any step fails, the PR gets a red X and can't be merged.

---

## 3. CD — Continuous Delivery / Deployment

Once CI passes, CD takes the code and puts it somewhere users can access it.

**Continuous Delivery** = code is automatically prepared for release, but a
human clicks "deploy."

**Continuous Deployment** = code is automatically deployed to production with
no human step. Every merged PR goes live.

### Typical CD flow:

```
CI passes on main branch
    |
    v
Build a Docker image (or bundle)
    |
    v
Push image to a registry (Docker Hub, ECR, GCR)
    |
    v
Deploy to staging (a copy of production for testing)
    |
    v
Run smoke tests / end-to-end tests against staging
    |
    v
Deploy to production
```

### Common deployment targets:

| Target | What it is | Example |
|--------|-----------|---------|
| **VPS** | A server you rent and SSH into | DigitalOcean, Linode |
| **PaaS** | Platform that runs your code for you | Heroku, Railway, Render |
| **Containers** | Packaged app with all its dependencies | Docker + Kubernetes |
| **Serverless** | Functions that run on demand | AWS Lambda, Vercel |
| **Static hosting** | For frontend-only apps | Netlify, GitHub Pages, Cloudflare Pages |

---

## 4. Environments

Most teams have multiple copies of their app:

| Environment | Purpose |
|-------------|---------|
| **Local / dev** | Your laptop. You run the app here while coding. |
| **Staging** | A copy of production. Used to test before going live. |
| **Production** | The real thing. Users are on this. |

Each environment usually has its own database, API keys, and config. These are
managed with **environment variables** (not hardcoded in code).

---

## 5. Other things you'll hear about

### Docker
Packages your app + its dependencies into a **container** — a lightweight,
reproducible box. "Works on my machine" goes away because everyone runs the
same container.

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install -e .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0"]
```

### Infrastructure as Code (IaC)
Instead of clicking around in AWS console, you define your servers, databases,
and networking in code files. Tools: Terraform, Pulumi, AWS CDK.

### Monitoring & Observability
Once your app is in production, you need to know if it's healthy:
- **Logging** — structured logs (not just `print()`) — tools: Datadog, Grafana
- **Metrics** — request count, latency, error rate — tools: Prometheus, Grafana
- **Alerting** — get paged if error rate spikes — tools: PagerDuty, OpsGenie
- **Tracing** — follow a request across multiple services — tools: Jaeger, Honeycomb

### Feature flags
Ship code that's turned off by default. Flip a switch to enable it for 1% of
users, then 10%, then 100%. If it breaks, flip it off instantly without
deploying. Tools: LaunchDarkly, Unleash.

---

## How this relates to RepoEvolve

RepoEvolve doesn't have a CI pipeline yet. If you wanted to add one, a minimal
version would be:

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync --group dev
      - run: uv run ruff check backend/
      - run: uv run pytest backend/tests/
```

This would automatically run the linter and tests on every push — catching
bugs before they reach `main`.

---

## TL;DR

| Term | One-liner |
|------|-----------|
| **CI** | Automated tests that run on every push |
| **CD** | Automated deployment after tests pass |
| **Pipeline** | The full sequence: push -> test -> build -> deploy |
| **PR** | A proposal to merge code, reviewed by teammates |
| **Docker** | Packages your app so it runs the same everywhere |
| **Staging** | A test copy of production |
| **Monitoring** | Watching your app in production for problems |
