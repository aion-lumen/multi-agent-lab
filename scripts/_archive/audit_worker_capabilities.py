#!/usr/bin/env python3
"""
audit_worker_capabilities.py - Static analysis of Worker + Bridge scripts.
Catalogues subprocess calls, HTTP endpoints, file opens, env-var reads.
Read-only, no execution.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "scripts"
TARGETS = {
    "production_worker": ROOT / "production_worker.py",
    "watchdog_bridge": ROOT / "watchdog_bridge.py",
    "sender_heuristic": ROOT / "sender_heuristic.py",
    "probe_worker": ROOT / "probe_worker.py",
}


def _stringify_func(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    if isinstance(node, ast.Name):
        return node.id
    return "<expr>"


def analyze_file(path: Path) -> dict:
    if not path.exists():
        return {"error": "not found"}
    code = path.read_text()
    tree = ast.parse(code)

    subprocess_calls: list[str] = []
    http_calls: list[str] = []
    file_opens: list[str] = []
    raw_urls: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = _stringify_func(node.func)

        # subprocess.run, subprocess.Popen etc.
        if fn.startswith("subprocess.") or fn in ("subprocess", "run"):
            cmd_parts: list[str] = []
            if node.args and isinstance(node.args[0], ast.List):
                for n in node.args[0].elts:
                    if isinstance(n, ast.Constant):
                        cmd_parts.append(str(n.value))
                    elif isinstance(n, ast.Name):
                        cmd_parts.append(f"<var:{n.id}>")
                    elif isinstance(n, ast.Starred):
                        cmd_parts.append("<*args>")
                    else:
                        cmd_parts.append("<dyn>")
            elif node.args and isinstance(node.args[0], ast.Constant):
                cmd_parts.append(str(node.args[0].value))
            else:
                cmd_parts.append("<dynamic>")
            subprocess_calls.append(" ".join(cmd_parts)[:120])

        elif fn.startswith("requests.") or fn in ("post", "get", "put", "delete"):
            if node.args and isinstance(node.args[0], ast.Constant):
                http_calls.append(str(node.args[0].value)[:120])
                raw_urls.add(str(node.args[0].value))
            else:
                http_calls.append("<dynamic URL>")

        elif fn == "open":
            if node.args and isinstance(node.args[0], ast.Constant):
                file_opens.append(str(node.args[0].value)[:120])

    env_reads = set()
    for m in re.finditer(r"os\.environ(?:\[|\.get\(['\"])(['\"]?)([A-Z][A-Z_0-9]+)\1", code):
        env_reads.add(m.group(2))

    # also search for sys.path additions / dangerous patterns
    sys_path_inserts = len(re.findall(r"sys\.path\.(?:insert|append)\(", code))
    eval_calls = len(re.findall(r"\beval\s*\(|\bexec\s*\(", code))

    return {
        "loc": len(code.splitlines()),
        "subprocess_calls": sorted(set(subprocess_calls)),
        "http_endpoints": sorted(set(http_calls)),
        "file_opens": sorted(set(file_opens)),
        "env_reads": sorted(env_reads),
        "sys_path_inserts": sys_path_inserts,
        "eval_exec_calls": eval_calls,
        "raw_urls": sorted(raw_urls),
    }


def main() -> None:
    print("=" * 70)
    print("WORKER-CAPABILITIES-AUDIT")
    print("=" * 70)
    for label, path in TARGETS.items():
        an = analyze_file(path)
        print(f"\n### {label} ({path.name})")
        print("-" * 70)
        if "error" in an:
            print(f"  {an['error']}")
            continue
        print(f"  LOC: {an['loc']}")
        print(f"  eval/exec calls: {an['eval_exec_calls']} (must be 0)")
        print(f"  sys.path modifications: {an['sys_path_inserts']}")
        print(f"\n  Subprocess-Calls ({len(an['subprocess_calls'])}):")
        for c in an['subprocess_calls']:
            print(f"    - {c}")
        print(f"\n  HTTP-Endpoints ({len(an['http_endpoints'])}):")
        for c in an['http_endpoints']:
            print(f"    - {c}")
        print(f"\n  File-Opens (literal paths, {len(an['file_opens'])}):")
        for c in an['file_opens'][:20]:
            print(f"    - {c}")
        if len(an['file_opens']) > 20:
            print(f"    ... +{len(an['file_opens']) - 20} more")
        print(f"\n  Env-Reads ({len(an['env_reads'])}):")
        for c in an['env_reads']:
            print(f"    - {c}")

    print("\n" + "=" * 70)
    print("WORST-CASE-ASSESSMENT (current, pre-Phase-4):")
    print("=" * 70)
    print("Worker can technically:")
    print("  1. invoke hermes-CLI (kanban subcommands)")
    print("  2. HTTP to LM Studio localhost:1234")
    print("  3. HTTPS to api.telegram.org (bridge only)")
    print("  4. read/write under multi-agent/state/")
    print("  5. read sender_heuristic.py + sender-heuristics.json")
    print()
    print("Worker can NOT (currently):")
    print("  - File-System ops outside multi-agent/state/")
    print("  - Network outside localhost + Telegram-API")
    print("  - eval/exec (verified above)")
    print("  - Subprocess outside hermes-CLI")
    print()
    print("PHASE 4 RISKS (when File-Move-Actions arrive):")
    print("  - Mail body could try to instruct IMAP operations")
    print("  - Vault writes become attack surface")
    print("  - Any new subprocess command becomes attack surface")


if __name__ == "__main__":
    main()
