from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from local_agent.tools.wix import WixClient, audit_blog_posts


class ToolExecutor:
    def __init__(self, workspace_root: str | Path = "."):
        self.workspace_root = Path(workspace_root).resolve()

    @staticmethod
    def _safe_int(raw_value, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = int(default)
        if minimum is not None and value < minimum:
            value = minimum
        if maximum is not None and value > maximum:
            value = maximum
        return value

    def execute(self, tool_calls: list[dict] | None) -> list[dict]:
        results: list[dict] = []
        for call in (tool_calls or [])[:5]:
            call = self._normalize_call(call)
            tool_name = str(call.get("tool", "")).strip().lower()
            if not tool_name:
                continue
            handler = getattr(self, f"_tool_{tool_name}", None)
            if handler is None:
                results.append({"tool": tool_name, "ok": False, "output": "Unsupported tool"})
                continue
            try:
                results.append({"tool": tool_name, "ok": True, "output": handler(call)})
            except Exception as exc:
                results.append({"tool": tool_name, "ok": False, "output": str(exc)})
        return results

    def _normalize_call(self, call: dict) -> dict:
        """Normalize planner-generated tool calls.

        Some plans emit {"tool": "run_command", "args": {...}} while tool
        handlers expect flattened keys. This keeps execution robust.
        """
        if not isinstance(call, dict):
            return {}
        normalized = dict(call)
        nested_args = normalized.get("args")
        if isinstance(nested_args, dict):
            for key, value in nested_args.items():
                normalized.setdefault(str(key), value)
        return normalized

    def _resolve_path(self, raw_path: str | None) -> Path:
        if not raw_path:
            return self.workspace_root
        raw = str(raw_path).strip()
        lowered = raw.lower().replace("\\", "/")

        # Support user-friendly desktop-relative paths from chat commands,
        # e.g. "desktop/RW_Media" -> "%USERPROFILE%/Desktop/RW_Media".
        if lowered == "desktop" or lowered.startswith("desktop/"):
            tail = raw.replace("\\", "/").split("/", 1)
            relative_tail = tail[1] if len(tail) > 1 else ""
            candidate = Path.home() / "Desktop"
            if relative_tail:
                candidate = candidate / relative_tail
            return candidate.resolve()

        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        return candidate.resolve()

    def _tool_list_files(self, call: dict) -> str:
        root = self._resolve_path(call.get("path"))
        if not root.exists():
            raise FileNotFoundError(f"Path not found: {root}")
        entries = sorted(root.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        limit = self._safe_int(call.get("limit", 40), default=40, minimum=1, maximum=500)
        limited = entries[:limit]
        lines = []
        for entry in limited:
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{entry}{suffix}")
        return "\n".join(lines) or "No entries"

    def _tool_read_file(self, call: dict) -> str:
        path = self._resolve_path(call.get("path"))
        start = self._safe_int(call.get("start_line", 1), default=1, minimum=1)
        end = self._safe_int(call.get("end_line", start + 50), default=start + 50, minimum=start)
        lines = path.read_text(encoding="utf-8").splitlines()
        excerpt = lines[start - 1 : end]
        return "\n".join(excerpt)

    def _tool_write_file(self, call: dict) -> str:
        path = self._resolve_path(call.get("path"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(call.get("content", "")), encoding="utf-8")
        return f"Wrote {path}"

    def _tool_replace_text(self, call: dict) -> str:
        path = self._resolve_path(call.get("path"))
        old_text = str(call.get("old_text", ""))
        new_text = str(call.get("new_text", ""))
        if not old_text:
            raise ValueError("replace_text requires old_text")
        text = path.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count != 1:
            raise ValueError(f"replace_text expected exactly one match, found {count}")
        path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Replaced text in {path}"

    def _tool_make_directory(self, call: dict) -> str:
        path = self._resolve_path(call.get("path"))
        path.mkdir(parents=True, exist_ok=True)
        return f"Created directory {path}"

    def _tool_delete_file(self, call: dict) -> str:
        path = self._resolve_path(call.get("path"))
        if path.is_dir():
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_file() or child.is_symlink():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    child.rmdir()
            path.rmdir()
        else:
            path.unlink(missing_ok=True)
        return f"Deleted {path}"

    def _tool_rename_path(self, call: dict) -> str:
        source = self._resolve_path(call.get("path"))
        target = self._resolve_path(call.get("target"))
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        return f"Renamed {source} -> {target}"

    def _tool_install_python_packages(self, call: dict) -> str:
        packages = call.get("packages") or call.get("package_list") or call.get("package")
        if isinstance(packages, str):
            package_list = [item.strip() for item in packages.split(",") if item.strip()]
        elif isinstance(packages, list):
            package_list = [str(item).strip() for item in packages if str(item).strip()]
        else:
            package_list = []
        if not package_list:
            raise ValueError("install_python_packages requires packages")

        command = [sys.executable, "-m", "pip", "install", *package_list]
        completed = subprocess.run(
            command,
            cwd=str(self.workspace_root),
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=min(max(int(call.get("timeout", 180)), 1), 600),
            shell=False,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        parts = [f"exit_code={completed.returncode}", f"command={' '.join(command)}"]
        if stdout:
            parts.append("stdout:\n" + stdout[:3000])
        if stderr:
            parts.append("stderr:\n" + stderr[:2000])
        return "\n\n".join(parts)

    def _tool_launch_executable(self, call: dict) -> str:
        """Launch a Windows executable and report whether it started."""
        exe_path = self._resolve_path(call.get("path") or call.get("exe_path"))
        if not exe_path.exists():
            raise FileNotFoundError(f"Executable not found: {exe_path}")
        if exe_path.suffix.lower() != ".exe":
            raise ValueError("launch_executable requires a .exe path")

        args = call.get("args") or []
        if isinstance(args, str):
            args = args.split()
        wait_seconds = min(max(int(call.get("wait_seconds", 2)), 0), 10)

        process = subprocess.Popen(
            [str(exe_path), *[str(arg) for arg in args]],
            cwd=str(exe_path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        if wait_seconds:
            time.sleep(wait_seconds)

        exit_code = process.poll()
        if exit_code is not None:
            # Non-zero exit is a genuine failure — don't second-guess it.
            if exit_code != 0:
                raise RuntimeError(
                    f"Executable exited with non-zero exit code (exit_code={exit_code}). "
                    f"path={exe_path}"
                )

            # exit_code == 0: common for bootstrap launchers that spawn a child and exit cleanly.
            # Check whether a process matching the exe stem is still running.
            exe_stem = exe_path.stem.lower()  # e.g. "alibredesign"
            child_running = False
            verification_method = "tasklist_name_match"
            try:
                tasklist = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                for line in tasklist.stdout.splitlines():
                    name_field = line.split(",")[0].strip('"').lower()
                    if exe_stem in name_field:
                        child_running = True
                        break
            except Exception:
                pass

            if not child_running:
                raise RuntimeError(
                    f"Executable exited cleanly (exit_code=0) but no running process "
                    f"matching '{exe_stem}' was found. path={exe_path}"
                )

            payload = {
                "started": True,
                "running": True,
                "pid": process.pid,
                "path": str(exe_path),
                "args": [str(arg) for arg in args],
                "verification_method": verification_method,
                "note": "Parent process exited cleanly; child process verified running by name.",
            }
            return json.dumps(payload)

        payload = {
            "started": True,
            "running": True,
            "pid": process.pid,
            "path": str(exe_path),
            "args": [str(arg) for arg in args],
            "verification_method": "process_poll",
        }
        return json.dumps(payload)

    def _tool_append_file(self, call: dict) -> str:
        path = self._resolve_path(call.get("path"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(str(call.get("content", "")))
        return f"Appended {path}"

    def _tool_search_text(self, call: dict) -> str:
        root = self._resolve_path(call.get("path"))
        query = str(call.get("query", "")).lower().strip()
        if not query:
            raise ValueError("search_text requires query")
        if not root.exists():
            raise FileNotFoundError(f"Path not found: {root}")
        limit = self._safe_int(call.get("limit", 20), default=20, minimum=1, maximum=500)
        matches: list[str] = []

        candidate_paths: list[Path] = []
        if root.is_file():
            candidate_paths.append(root)
        else:
            for dirpath, _, filenames in os.walk(root, onerror=lambda _exc: None):
                for filename in filenames:
                    candidate_paths.append(Path(dirpath) / filename)

        for path in candidate_paths:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for idx, line in enumerate(lines, start=1):
                if query in line.lower():
                    matches.append(f"{path}:{idx}: {line.strip()}")
                    if len(matches) >= limit:
                        return "\n".join(matches)
        return "\n".join(matches) or "No matches"

    def _tool_execute_python(self, call: dict) -> str:
        """
        Execute a Python script file that exists in the workspace and return
        its stdout/stderr/exit code.
        Args:
          path:    relative path to the .py file to run
          args:    optional list of CLI args to pass
          timeout: seconds (default 30, max 120)
        """
        path = self._resolve_path(call.get("path"))
        if not path.exists():
            raise FileNotFoundError(f"Script not found: {path}")
        if path.suffix.lower() != ".py":
            raise ValueError("execute_python only runs .py files")
        args = call.get("args") or []
        if isinstance(args, str):
            args = args.split()
        timeout = min(max(int(call.get("timeout", 30)), 1), 120)
        command = [sys.executable, str(path)] + [str(a) for a in args]
        completed = subprocess.run(
            command,
            cwd=str(self.workspace_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        parts = [f"exit_code={completed.returncode}"]
        if completed.stdout.strip():
            parts.append("stdout:\n" + completed.stdout.strip()[:3000])
        if completed.stderr.strip():
            parts.append("stderr:\n" + completed.stderr.strip()[:1500])
        return "\n\n".join(parts)

    def _tool_check_capability(self, call: dict) -> str:
        """
        Check whether a Python package or CLI command is available.
        Args:
          kind: "package" | "command"  (default: "package")
          name: name to check (e.g. "requests" or "ffmpeg")
        Returns a JSON-like string: {"available": true/false, "detail": "..."}
        """
        import importlib.util
        kind = str(call.get("kind", "package")).strip().lower()
        name = str(call.get("name", "")).strip()
        if not name:
            raise ValueError("check_capability requires 'name'")
        if kind == "command":
            found = shutil.which(name)
            if found:
                return f'{{"available": true, "detail": "{found}"}}'
            return f'{{"available": false, "detail": "command not found: {name}"}}'
        # package check: try importlib first, then pip list
        spec = importlib.util.find_spec(name.replace("-", "_").split("[")[0])
        if spec is not None:
            return f'{{"available": true, "detail": "{spec.origin}"}}'
        # also check pip metadata so namespace packages are caught
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", name],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            loc = next((l for l in result.stdout.splitlines() if l.startswith("Location:")), "")
            return f'{{"available": true, "detail": "{loc.strip()}"}}'
        return f'{{"available": false, "detail": "package not installed: {name}"}}'

    def _tool_scaffold_tool(self, call: dict) -> str:
        """
        Create a new Python helper/tool script in the workspace.
        Args:
          path:    relative path for the new file (e.g. "tools/my_tool.py")
          purpose: one-sentence description — prepended as a docstring comment
          code:    full Python source to write (must be provided)
        """
        path = self._resolve_path(call.get("path"))
        purpose = str(call.get("purpose", "Generated tool")).strip()
        code = str(call.get("code", "")).strip()
        if not code:
            raise ValueError("scaffold_tool requires 'code'")
        header = f'"""\n{purpose}\n\nGenerated by local-agent scaffold_tool.\n"""\n'
        if not code.startswith('"""'):
            code = header + code
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        return f"Scaffolded {path} ({len(code)} chars)"

    def _tool_download_file(self, call: dict) -> str:
        """
        Download a file from a URL to a local workspace path.
        Args:
          url:  the HTTPS URL to fetch
          path: local destination path (relative to workspace root)
        Security: only HTTPS URLs are allowed; redirects are followed.
        """
        url = str(call.get("url", "")).strip()
        if not url:
            raise ValueError("download_file requires 'url'")
        if not url.lower().startswith("https://"):
            raise ValueError("download_file only allows HTTPS URLs")
        dest = self._resolve_path(call.get("path"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, dest)  # noqa: S310 — HTTPS-only guard above
        size = dest.stat().st_size
        return f"Downloaded {url} → {dest} ({size} bytes)"

    def _wix_client(self) -> WixClient:
        """Build a Wix client from the repo's standard env-based config."""
        return WixClient.from_env()

    @staticmethod
    def _coerce_mapping(value, field: str) -> dict:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise ValueError(f"{field} must be a JSON object")
            return parsed
        raise ValueError(f"{field} requires a JSON object")

    def _tool_wix_seo_audit(self, call: dict) -> str:
        """Read-only SEO audit of Wix blog content. Never mutates the site."""
        limit = int(call.get("limit", 50))
        client = self._wix_client()
        posts = client.list_blog_posts(limit=limit)
        report = audit_blog_posts(posts)
        payload = {"report": report.to_dict(), "summary": report.summary()}
        return json.dumps(payload)

    def _tool_wix_create_draft_post(self, call: dict) -> str:
        """Create a Wix blog DRAFT post. Drafts are never published by Chad."""
        draft_post = self._coerce_mapping(call.get("draft_post"), "draft_post")
        client = self._wix_client()
        result = client.create_draft_post(draft_post)
        return json.dumps(result)

    def _tool_wix_update_draft_post(self, call: dict) -> str:
        """Update an existing Wix blog DRAFT post. Stays a draft; never published."""
        draft_post_id = str(call.get("draft_post_id") or call.get("id") or "").strip()
        if not draft_post_id:
            raise ValueError("wix_update_draft_post requires draft_post_id")
        draft_post = self._coerce_mapping(call.get("draft_post"), "draft_post")
        client = self._wix_client()
        result = client.update_draft_post(draft_post_id, draft_post)
        return json.dumps(result)

    def _tool_run_command(self, call: dict) -> str:
        command = str(call.get("command", "")).strip()
        if not command:
            raise ValueError("run_command requires command")
        if "\n" in command or "\r" in command:
            raise ValueError("run_command only accepts single-line commands")
        self._validate_command(command)
        cwd = self._resolve_path(call.get("cwd"))
        timeout = min(max(int(call.get("timeout", 20)), 1), 120)
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        parts = [f"exit_code={completed.returncode}"]
        if stdout:
            parts.append("stdout:\n" + stdout[:2000])
        if stderr:
            parts.append("stderr:\n" + stderr[:1200])
        output = "\n\n".join(parts)
        if completed.returncode != 0:
            raise RuntimeError(output)
        return output

    def _validate_command(self, command: str) -> None:
        lowered = command.lower()
        blocked = [
            "format ",
            "shutdown",
            "restart-computer",
            "stop-computer",
            "mkfs",
            "diskpart",
            "del /f /s /q c:\\",
            "rm -rf /",
            "cipher /w",
        ]
        for token in blocked:
            if token in lowered:
                raise ValueError(f"Blocked command pattern: {token}")