#!/bin/bash
cd /root/upgraded-journey

# Clean up any existing processes/screens
pkill -9 -f gunicorn 2>/dev/null || true
pkill -9 -f cloudflared 2>/dev/null || true
screen -X -S gunicorn quit 2>/dev/null || true
screen -X -S tunnel quit 2>/dev/null || true
sleep 1

# Start Gunicorn in screen 'gunicorn'
screen -dmS gunicorn bash -c "PORT=5000 python3 -m gunicorn -b 127.0.0.1:5000 -c gunicorn.conf.py app:app 2>&1 | tee /root/upgraded-journey/gunicorn.log"

# Wait for gunicorn to bind to port 5000
for i in {1..10}; do
  if curl -s http://127.0.0.1:5000/api/trending >/dev/null 2>&1; then
    echo "Gunicorn is UP on http://127.0.0.1:5000"
    break
  fi
  sleep 1
done

# Start Cloudflare Tunnel in screen 'tunnel'
> /root/upgraded-journey/tunnel.log
screen -dmS tunnel bash -c "cloudflared tunnel --protocol http2 --url http://127.0.0.1:5000 2>&1 | tee /root/upgraded-journey/tunnel.log"

# Wait for tunnel URL to appear in tunnel.log
echo "Waiting for Cloudflare Tunnel URL..."
TUNNEL_URL=""
for i in {1..30}; do
  TUNNEL_URL=$(grep -o 'https://[-a-zA-Z0-9.]*\.trycloudflare\.com' /root/upgraded-journey/tunnel.log | head -n 1)
  if [ -n "$TUNNEL_URL" ]; then
    break
  fi
  sleep 1
done

if [ -n "$TUNNEL_URL" ]; then
  echo "Tunnel is LIVE: $TUNNEL_URL"
  echo "$TUNNEL_URL" > /root/upgraded-journey/tunnel_url.txt
  DOMAIN=$(echo "$TUNNEL_URL" | sed 's|https://||')
  # Resolve via Cloudflare DoH and add to /etc/hosts for local container resolution
  python3 -c "
import urllib.request, json
try:
    req = urllib.request.Request('https://cloudflare-dns.com/dns-query?name=${DOMAIN}&type=A', headers={'Accept': 'application/dns-json'})
    with urllib.request.urlopen(req, timeout=5) as r:
        d = json.loads(r.read().decode())
        ip = d.get('Answer', [{}])[0].get('data')
        if ip:
            with open('/etc/hosts', 'a') as f:
                f.write(f'\n{ip} ${DOMAIN}\n')
except Exception as e:
    pass
"
else
  echo "Failed to find tunnel URL in 30 seconds. Check /root/upgraded-journey/tunnel.log"
fi
