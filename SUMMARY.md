# Local Agent AI Operations Platform - High Level Summary

## Run Commands

- Start Telegram bot: `c:/Users/platt/Desktop/local-agent/.venv/Scripts/python.exe scripts/run_telegram_bot.py`
- Clean restart Telegram bot from repo root: `python clean_restart.py`

## Purpose

This repository implements a Windows-first AI operations assistant platform designed to manage real business work across multiple workspaces. It targets a general-purpose orchestration system rather than a narrow business-planner bot.

The platform supports multiple primary businesses, including Rocket Wash (a pressure washing business) and Raze Development Studios (a React-based product configurator software company).

## Core Capabilities

- Runs a hosted high-capability orchestrator model for primary planning and synthesis.
- Delegates specialist work to configurable agents and model providers.
- Supports local AI models (e.g., Ollama) with flexible quality tradeoffs.
- Provides workspace-aware context priming per chat/session.
- Enables retrieval with source citations over repo files, prompts, and workspace documents.
- Supports guarded local execution of filesystem operations, scripts, package installs, and commands.
- Uses a centralized approval flow for risky actions.
- Supports remote operation through Telegram (phase 1).

## Architecture Overview

The system is composed of core modules including:

- `src/local_agent/orchestration/admin.py`: Central orchestration loop handling admin and work requests.
- `src/local_agent/orchestration/planner.py`: LLM-based planner that returns structured routing and tool decisions.
- `src/local_agent/orchestration/supervisor.py`: Quality gatekeeper that critiques outputs and enforces policies.
- `src/local_agent/llm/router.py`: Routes requests to appropriate LLM providers.
- `agents/`: Contains agent persona YAML files defining agent identities, behaviors, and tasks.

The platform is designed for extensibility with configurable agents and supports multi-agent orchestration workflows.

## Main Components

- **Orchestrator**: Coordinates planning, execution, and supervision of tasks.
- **Planner**: Uses LLMs to generate structured plans and tool routing.
- **Supervisor**: Enforces quality gates and policy compliance on outputs.
- **Agent Personas**: YAML-defined agents with specific roles and behaviors.
- **Local AI Models**: Integration with local models like Ollama for flexible deployment.
- **Execution Environment**: Supports guarded execution of scripts and commands with approval flows.

## Blockers

- None
 Expected output: First 20 lines of the summary showing Purpose and Core Capabilities sections.
 Expected output: Last 10 lines showing Blockers section with "None".
