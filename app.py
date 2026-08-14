"""
Flask Movie App — proxies requests to the MovieBox upstream API and serves a
dark, Netflix-style frontend. The CDN streaming endpoint proxies Range
requests back to the browser so HTML5 video playback works despite the
CDN User-Agent block. Streams are served natively (HEVC/H.264); no
server-side transcoding.
"""

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    Response,
    send_from_directory,
)
import json
import movie_api as api

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/watch/<subject_id>")
def watch(subject_id):
    """Dedicated detail page with a direct player and in-player quality/dub controls."""
    return render_template("detail_new.html", subject_id=subject_id)


@app.route("/detail-demo")
def detail_demo():
    """Standalone demo of the redesigned watch page (all-in-one HTML/CSS/JS)."""
    return render_template("detail_new.html")


# ---------------------------------------------------------------------------
# API proxy routes (called by the frontend via fetch())
# ---------------------------------------------------------------------------
@app.route("/api/search")
def search():
    keyword = request.args.get("q", "").strip()
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 20))
    subject_type = int(request.args.get("type", 0))
    if not keyword:
        return jsonify({"error": "Empty search keyword"}), 400
    try:
        data = api.search(keyword, page=page, per_page=per_page, subject_type=subject_type)
        return jsonify(data)
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/suggest")
def suggest():
    keyword = request.args.get("q", "").strip()
    try:
        return jsonify(api.search_suggest(keyword))
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/detail/<subject_id>")
def detail(subject_id):
    try:
        return jsonify(api.subject_detail(subject_id))
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/seasons/<subject_id>")
def seasons(subject_id):
    try:
        return jsonify(api.season_info(subject_id))
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


from concurrent.futures import ThreadPoolExecutor

@app.route("/api/resources_dubs")
def resources_dubs():
    """Fetch resources for every dub of a title concurrently. Each dub in detail.dubs
    has its own subjectId; this returns {dubs: [{lanName, lanCode, subjectId, list}]}."""
    subject_id = request.args.get("subjectId", "").strip()
    se = request.args.get("se", "0")
    ep = request.args.get("ep", "0")
    if not subject_id:
        return jsonify({"error": "Missing subjectId"}), 400
    try:
        detail = api.subject_detail(subject_id)
        d = detail.get("data", detail)
        dubs = d.get("dubs", [])
        if not dubs:
            return jsonify({"dubs": []})

        def fetch_dub(dub):
            dub_id = dub.get("subjectId")
            entry = {"lanName": dub.get("lanName"), "lanCode": dub.get("lanCode"), "subjectId": dub_id, "list": []}
            if dub_id:
                try:
                    r = api.get_resources(dub_id, se=se, ep=ep)
                    entry["list"] = r.get("data", r).get("list", [])
                except Exception:  # noqa
                    pass
            return entry

        with ThreadPoolExecutor(max_workers=min(len(dubs), 8)) as executor:
            out = list(executor.map(fetch_dub, dubs))

        return jsonify({"dubs": out})
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/resources")
def resources():
    subject_id = request.args.get("subjectId", "").strip()
    se = request.args.get("se", "0")
    ep = request.args.get("ep", "0")
    if not subject_id:
        return jsonify({"error": "Missing subjectId"}), 400
    try:
        return jsonify(api.get_resources(subject_id, se=se, ep=ep))
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/subtitles")
def subtitles():
    """Extract subtitles/captions available for a title or episode."""
    subject_id = request.args.get("subjectId", "").strip()
    se = request.args.get("se", "0")
    ep = request.args.get("ep", "0")
    if not subject_id:
        return jsonify({"error": "Missing subjectId"}), 400
    try:
        r = api.get_resources(subject_id, se=se, ep=ep)
        res_list = r.get("data", {}).get("list", [])
        sub_list = []
        seen = set()
        for item in res_list:
            caps = item.get("extCaptions") or item.get("subtitles") or item.get("captions") or []
            if isinstance(caps, list):
                for c in caps:
                    url = c.get("url") or c.get("subPath") or c.get("captionUrl")
                    lang = c.get("language") or c.get("lanName") or c.get("lanCode") or "Subtitle"
                    if url and url not in seen:
                        seen.add(url)
                        sub_list.append({"language": lang, "url": url})
        return jsonify({"subtitles": sub_list})
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/image_proxy")
def image_proxy():
    """Proxy image assets (posters/avatars) to bypass CORS and hotlink protection."""
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing image URL"}), 400
    try:
        resp = api._session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if resp.status_code == 200:
            return Response(resp.content, status=200, content_type=resp.headers.get("Content-Type", "image/jpeg"))
        return jsonify({"error": f"Upstream error {resp.status_code}"}), resp.status_code
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/download")
def download():
    """Full download (200, no Range) with Content-Disposition header."""
    url = request.args.get("url", "")
    if not url or "hakunaymatata.com" not in url and "aoneroom" not in url:
        return jsonify({"error": "Invalid stream URL"}), 400
    title = request.args.get("title", "video")
    filename = title.replace("/", "_").replace('"', "")

    headers = {"User-Agent": api.ANDROID_USER_AGENT}
    resp = api._session.get(url, headers=headers, stream=True, timeout=20)
    ext = "mp4"
    content_type = resp.headers.get("Content-Type", "video/mp4")
    forwarded = Response(resp.iter_content(chunk_size=1024 * 512),
                         status=resp.status_code,
                         content_type=content_type)
    forwarded.headers["Content-Length"] = resp.headers.get("Content-Length", "")
    forwarded.headers["Content-Disposition"] = f'attachment; filename="{filename}.{ext}"'
    forwarded.headers["Accept-Ranges"] = "bytes"
    return forwarded


@app.route("/api/trending")
def trending():
    try:
        return jsonify(api.trending())
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


import shutil
import subprocess

# ---------------------------------------------------------------------------
# CDN video proxy — mandatory Android UA + Range forwarding
# ---------------------------------------------------------------------------
@app.route("/stream")
def stream():
    url = request.args.get("url", "")
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"error": "Invalid stream URL"}), 400

    range_header = request.headers.get("Range")
    client_ip = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For") or request.remote_addr

    headers = {
        "User-Agent": api.ANDROID_USER_AGENT,
        "X-Forwarded-For": client_ip,
        "X-Real-IP": client_ip,
        "CF-Connecting-IP": client_ip,
        "Client-IP": client_ip,
    }
    if range_header:
        headers["Range"] = range_header

    try:
        resp = api._session.get(url, headers=headers, stream=True, timeout=15)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    status_code = resp.status_code
    if status_code not in (200, 206):
        return jsonify({"error": f"Upstream returned {status_code}"}), status_code

    def generate():
        try:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if chunk:
                    yield chunk
        except Exception:
            pass

    forwarded = Response(
        generate(),
        status=status_code,
        content_type=resp.headers.get("Content-Type", "video/mp4")
    )

    for h in ("Content-Range", "Content-Length", "Accept-Ranges", "Cache-Control"):
        if h in resp.headers:
            forwarded.headers[h] = resp.headers[h]

    forwarded.headers["Access-Control-Allow-Origin"] = "*"
    forwarded.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    forwarded.headers["Access-Control-Allow-Headers"] = "Range, User-Agent"
    return forwarded


@app.route("/stream_transcode")
def stream_transcode():
    """On-the-fly H.264 video transcoding with fast seeking using ffmpeg."""
    url = request.args.get("url", "")
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"error": "Invalid stream URL"}), 400

    # If ffmpeg is not available, fallback to direct proxy
    if not shutil.which("ffmpeg"):
        return stream()

    try:
        ss = request.args.get("ss", "0").strip()
        headers_str = f"User-Agent: {api.ANDROID_USER_AGENT}\r\n"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
        ]
        try:
            ss_val = float(ss)
            if ss_val > 0:
                cmd.extend(["-ss", str(ss_val)])
        except ValueError:
            pass

        cmd.extend([
            "-headers", headers_str,
            "-i", url,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-threads", "0",
            "-pix_fmt", "yuv420p",
            "-crf", "28",
            "-c:a", "copy",
            "-f", "mp4",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "pipe:1"
        ])

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=64 * 1024)

        def generate():
            try:
                while True:
                    data = proc.stdout.read(32 * 1024)
                    if not data:
                        break
                    yield data
            finally:
                try:
                    proc.terminate()
                except Exception:  # noqa
                    pass

        resp = Response(generate(), status=200, content_type="video/mp4")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except Exception:
        return stream()


# ---------------------------------------------------------------------------
# ServiceWorker — intercepts direct CDN video requests, injects User-Agent
# This bypasses the Flask proxy entirely for instant low-latency playback
# ---------------------------------------------------------------------------
@app.route("/sw.js")
def service_worker():
    js = f"""
const ANDROID_UA = {json.dumps(api.ANDROID_USER_AGENT)};

self.addEventListener('install', event => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

// Intercept ALL requests to hakunaymatata.com and aoneroom CDN
// This lets us inject the required Android User-Agent header
// so video streams directly from CDN to user with zero server hop
self.addEventListener('fetch', event => {{
    const urlStr = event.request.url;
    const isCdn = urlStr.includes('hakunaymatata.com') || urlStr.includes('aoneroom');

    if (isCdn) {{
        event.respondWith((async () => {{
            try {{
                const reqHeaders = new Headers();
                // Forward Range header for seek/partial content support
                const rangeHeader = event.request.headers.get('Range');
                if (rangeHeader) reqHeaders.set('Range', rangeHeader);
                reqHeaders.set('User-Agent', ANDROID_UA);
                reqHeaders.set('Referer', 'https://www.moviebox.ng/');
                reqHeaders.set('Origin', 'https://www.moviebox.ng');

                const cdnResp = await fetch(urlStr, {{
                    method: event.request.method,
                    headers: reqHeaders,
                    mode: 'cors',
                    credentials: 'omit'
                }});
                if (cdnResp.ok || cdnResp.status === 206) return cdnResp;
            }} catch (e) {{
                // SW fetch failed - fallback to server proxy
            }}
            return fetch(event.request);
        }})());
    }}
}});
"""
    return Response(js, content_type="application/javascript", headers={"Service-Worker-Allowed": "/"})


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------
@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)

