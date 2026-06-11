#!/usr/bin/env bash
# Expose local FastAPI for AWS Lambda notifier callbacks.
set -euo pipefail

PORT="${BASERENDER_API_PORT:-8000}"

if command -v cloudflared >/dev/null 2>&1; then
  echo "Starting cloudflared tunnel to http://127.0.0.1:${PORT}"
  cloudflared tunnel --url "http://127.0.0.1:${PORT}"
elif command -v ngrok >/dev/null 2>&1; then
  echo "Starting ngrok tunnel to http://127.0.0.1:${PORT}"
  ngrok http "$PORT"
else
  echo "Install cloudflared or ngrok, then re-run this script." >&2
  echo "  brew install cloudflared" >&2
  exit 1
fi
