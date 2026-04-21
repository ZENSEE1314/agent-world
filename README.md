# Agent World — Autonomous Survival Ecosystem

A live 2D simulation where 6+ LLM-driven agents compete and cooperate to survive. Each agent has a house, stats (health/hunger), a bank balance, a personality, and a "brain" that decides what to do next: work, eat, learn, trade, chat, or build projects.

**God View** dashboard lets you watch the world live, issue divine commands, and hot-add new houses / agents.

## Features

- 6 starter agents (Greek-pantheon codenames) + Hermes the Coder Mentor
- Health / Hunger decay loops, food market ($300/meal), death and revival
- Economy with a 10x real→game multiplier
- Agent-to-agent messaging and team-ups
- "Knowledge Request" protocol for mentorship from Hermes
- Projects folder (`projects/`) where agents save their work as markdown
- Obsidian-compatible memory vault (`memory/`) — one folder per agent
- Leaderboard with sales + health bars
- Hot-add houses / agents from the UI (click the ➕)
- LLM adapters: Mock (default, no key needed), Anthropic, OpenAI, Ollama
- WebSocket live feed; no page reload needed
- Railway-ready (`railway.toml`, `Dockerfile`)

## Quickstart (local)

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 in your browser.

By default it runs with the **Mock** brain (no API keys needed). To use real LLMs:

```bash
export BRAIN=anthropic   # or openai, ollama
export ANTHROPIC_API_KEY=sk-ant-...
# or OPENAI_API_KEY=..., or OLLAMA_HOST=http://localhost:11434
```

## Deploy to Railway

1. Push this repo to GitHub.
2. In Railway: **New → Deploy from GitHub repo → agent-world**.
3. Railway auto-detects the `Dockerfile`. Set env vars (`BRAIN`, `ANTHROPIC_API_KEY`, etc.) in the Variables tab.
4. Expose the service — Railway gives you a public URL.

## Layout

```
backend/
  main.py            FastAPI app + static mount
  engine/
    world.py         WorldEngine tick loop
    agent.py         Agent state + decay
    economy.py       EconomyManager
    brain.py         LLM adapters (mock / anthropic / openai / ollama)
    jobs.py          Money-making task registry
    messaging.py     Inter-agent message bus
    logger.py        Per-agent log files
  api/
    routes.py        REST endpoints
    ws.py            WebSocket fan-out
frontend/
  index.html         God View dashboard
  style.css          Isometric-ish world styling
  app.js             Canvas + WebSocket client
projects/            Agents save built projects here
memory/              Obsidian-friendly agent memory vault
logs/                Per-agent append-only logs
```
