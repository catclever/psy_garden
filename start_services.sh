#!/usr/bin/env bash
# ==============================================================================
# Psy Garden - Unified Service Launcher
# Launches Botanist Studio (Review: 18087) and Web Frontend (Preview: 18088)
# All bound to 0.0.0.0 inside a single session/tab.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUDIO_PORT="${BOTANIST_PORT:-18087}"
WEB_PORT="${WEB_PORT:-18088}"

echo "=================================================="
echo "🌱 Starting Psy Garden Unified Services..."
echo "  - Botanist Studio (审查终端): http://0.0.0.0:${STUDIO_PORT}"
echo "  - Psy Garden Web  (前端预览): http://0.0.0.0:${WEB_PORT}"
echo "=================================================="

# Function to clean up child processes on exit
cleanup() {
    echo ""
    echo "🛑 Stopping Psy Garden services..."
    if [[ -n "${STUDIO_PID:-}" ]] && kill -0 "$STUDIO_PID" 2>/dev/null; then
        kill "$STUDIO_PID" 2>/dev/null || true
    fi
    if [[ -n "${WEB_PID:-}" ]] && kill -0 "$WEB_PID" 2>/dev/null; then
        kill "$WEB_PID" 2>/dev/null || true
    fi
    # Also release ports in case subshells spawned children
    lsof -ti:"${STUDIO_PORT}","${WEB_PORT}" 2>/dev/null | xargs -r kill 2>/dev/null || true
    echo "✅ All services stopped."
}
trap cleanup SIGINT SIGTERM EXIT

# Ensure ports are clean before starting
lsof -ti:"${STUDIO_PORT}","${WEB_PORT}" 2>/dev/null | xargs -r kill 2>/dev/null || true

# 1. Start Botanist Studio (Review Terminal) on 18087
echo "🚀 Launching Botanist Studio on port ${STUDIO_PORT}..."
(
    cd "${SCRIPT_DIR}/tools"
    export PORT="${STUDIO_PORT}"
    if command -v uv >/dev/null 2>&1; then
        exec uv run python botanist_studio.py
    else
        exec python3 botanist_studio.py
    fi
) 2>&1 | sed -u 's/^/[Studio:18087] /' &
STUDIO_PID=$!

# 2. Start Static Web Preview on 18088
echo "🌐 Launching Psy Garden Web Preview on port ${WEB_PORT}..."
(
    cd "${SCRIPT_DIR}"
    exec python3 -m http.server "${WEB_PORT}" --bind 0.0.0.0 --directory src
) 2>&1 | sed -u 's/^/[Web:18088]    /' &
WEB_PID=$!

echo "✨ Both services are active in this session."
echo "Press Ctrl+C to terminate both services."

# Wait for both processes
wait -n "$STUDIO_PID" "$WEB_PID" || true
