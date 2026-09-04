#!/bin/bash
pkill -9 -f gunicorn 2>/dev/null || true
pkill -9 -f cloudflared 2>/dev/null || true
screen -X -S gunicorn quit 2>/dev/null || true
screen -X -S tunnel quit 2>/dev/null || true
echo "Stopped gunicorn and tunnel."
