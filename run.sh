#!/bin/sh
# Launcher for israelgrocery-mcp.
# Claude Desktop spawns processes with a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin)
# that does NOT include the directories where `uv` is typically installed.
# This script adds all common uv install locations before delegating to `uv run`.

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# Resolve the directory containing this script (handles spaces in path)
DIR="$(cd "$(dirname "$0")" && pwd)"

exec uv --directory "$DIR" run israelgrocery-mcp
