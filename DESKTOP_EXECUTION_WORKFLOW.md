# Desktop Executable Task Workflow

This runbook defines how local-agent should execute requests such as opening an executable from the desktop.

## Scope

- Request type: desktop execution (for example, "open the exe on my desktop").
- Expected behavior: tool execution path with evidence, not discussion-only output.

## Procedure

1. Detect execution intent.
- Route to `desktop_execution` and keep `selected_agent=none` unless there is a hard blocker.
- Require confirmation/approval before risky local execution.

2. Discover the target executable.
- Use `list_files` and/or `search_text` to locate exact `.exe` path.
- If multiple matches exist, pick the most explicit match from the request or ask for clarification.

3. Check supporting capability.
- Use `check_capability` for required commands/packages.
- If capability is missing, perform setup with:
  - `install_python_packages` for Python dependencies,
  - `download_file` for approved HTTPS artifacts,
  - `scaffold_tool` for local helper automation when needed.

4. Launch executable.
- Preferred: `launch_executable` with absolute/normalized path and optional args.
- Alternative: `run_command` only when direct executable launch is not suitable.

5. Verify success.
- Capture tool evidence indicating process start (for example: pid and running state).
- If launch fails, return failed status with command/tool output and actionable next step.

6. Record evidence.
- Persist `executed_tool_calls`, success/failure signals, and completion status to `state/interactions.jsonl`.
- Do not mark execution-intent tasks as completed without tool evidence.

## Acceptance Signals

- Route is `desktop_execution`.
- Tool path includes discovery + launch.
- Missing capability triggers install/setup path instead of deflection.
- Completion status is backed by execution evidence.