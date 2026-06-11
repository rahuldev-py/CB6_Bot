#!/usr/bin/env python3
"""
PreToolUse hook — blocks any edit that introduces paper_mode=True into live config/state files.
Called by Claude Code before every Edit tool call.

stdin: JSON with keys: tool_name, tool_input
stdout: JSON response (approve/block)
"""

import sys
import json

def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except Exception:
        # Can't parse input — let the edit proceed
        print(json.dumps({"approve": True}))
        return

    tool_name  = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    if tool_name != "Edit":
        print(json.dumps({"approve": True}))
        return

    file_path  = tool_input.get("file_path", "")
    new_string = tool_input.get("new_string", "")

    # Block paper_mode=True being introduced into any live file
    if "paper_mode" in new_string and "True" in new_string:
        # Allow it only in explicitly test/paper files
        safe_paths = ["paper", "test", "mock", "demo", "_dev"]
        is_safe = any(s in file_path.lower() for s in safe_paths)
        if not is_safe:
            print(json.dumps({
                "approve": False,
                "message": (
                    "🚫 BLOCKED: Attempted to set paper_mode=True in a live file.\n"
                    f"File: {file_path}\n"
                    "Live engines must never run in paper mode.\n"
                    "If this is intentional for testing, rename the file with 'test' or 'paper' in the path."
                )
            }))
            return

    # XAUUSD re-enabled on GFT accounts 2026-06-10 — H4 bias filter enforced in code.
    # Root cause of May 22 losses (trading SELL vs H4 uptrend) addressed at scanner level.

    print(json.dumps({"approve": True}))


if __name__ == "__main__":
    main()
