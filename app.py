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
    session,
    redirect,
)
import json
import os
from functools import wraps
import movie_api as api
import tracker

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "moviebox-admin-secret-key-2026")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin_auth"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------------------------
# Admin & Tracking Routes
# ---------------------------------------------------------------------------
@app.route("/admin", methods=["GET"])
def admin_page():
    is_logged_in = bool(session.get("admin_auth"))
    return render_template("admin.html", is_logged_in=is_logged_in)


@app.route("/admin/login", methods=["POST"])
def admin_login():
    user = request.form.get("username", "").strip()
    pwd = request.form.get("password", "").strip()
    if user == ADMIN_USERNAME and pwd == ADMIN_PASSWORD:
        session["admin_auth"] = True
        return redirect("/admin")
    return render_template("admin.html", is_logged_in=False, error="Invalid admin username or password.")


@app.route("/admin/logout", methods=["GET"])
def admin_logout():
    session.pop("admin_auth", None)
    return redirect("/admin")


@app.route("/api/admin/stats", methods=["GET"])
@admin_required
def admin_stats():
    return jsonify(tracker.get_stats())


@app.route("/api/admin/logs", methods=["GET"])
@admin_required
def admin_logs():
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 30))
    action = request.args.get("action", "ALL")
    search = request.args.get("search", "")
    return jsonify(tracker.get_logs(page=page, limit=limit, action=action, search=search))


@app.route("/api/admin/clear_logs", methods=["POST"])
@admin_required
def admin_clear_logs():
    success = tracker.clear_logs()
    return jsonify({"success": success})


@app.route("/api/track", methods=["POST"])
def api_track():
    """Client-side logging hook for watch events & heartbeats."""
    try:
        data = request.get_json(silent=True) or {}
        ip = tracker.get_client_ip(request)
        action = data.get("action", "STREAM_PING")
        subject_id = data.get("subject_id", "")
        media_title = data.get("title", "")
        season = data.get("season")
        episode = data.get("episode")
        quality = data.get("quality", "")
        details = data.get("details", "")
        ua = request.headers.get("User-Agent", "")
        tracker.log_activity(ip, action, subject_id, media_title, season, episode, quality, details, ua)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    ip = tracker.get_client_ip(request)
    tracker.log_activity(ip, "PAGE_VIEW", subject_id=subject_id, user_agent=request.headers.get("User-Agent", ""))
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
        ip = tracker.get_client_ip(request)
        tracker.log_activity(ip, "SEARCH", details=f"q={keyword}", user_agent=request.headers.get("User-Agent", ""))
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

    # Track download event
    ip = tracker.get_client_ip(request)
    tracker.log_activity(ip, "DOWNLOAD", media_title=title, details=f"filename={filename}", user_agent=request.headers.get("User-Agent", ""))

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
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-map_metadata", "-1",
            "-map_chapters", "-1",
            "-sn",
            "-dn",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-threads", "0",
            "-pix_fmt", "yuv420p",
            "-crf", "28",
            "-c:a", "aac",
            "-b:a", "128k",
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
# HLS (HTTP Live Streaming) Endpoints — Universal m3u8 playlist & TS segments
# ---------------------------------------------------------------------------
import urllib.parse

@app.route("/api/hls/playlist.m3u8")
def hls_playlist():
    """Dynamic HLS m3u8 playlist generator."""
    url = request.args.get("url", "")
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"error": "Invalid URL"}), 400

    dur_str = request.args.get("dur", "7200")
    try:
        dur = float(dur_str)
        if dur <= 0:
            dur = 7200.0
    except ValueError:
        dur = 7200.0

    segment_dur = 6.0
    num_segments = max(1, int(dur // segment_dur))
    if (dur % segment_dur) > 1.0:
        num_segments += 1

    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{int(segment_dur) + 1}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    encoded_url = urllib.parse.quote(url, safe='')
    for i in range(num_segments):
        ss = i * segment_dur
        seg_len = segment_dur if (i < num_segments - 1) else max(1.0, dur - ss)
        lines.append(f"#EXTINF:{seg_len:.2f},")
        lines.append(f"/api/hls/segment.ts?url={encoded_url}&ss={ss:.2f}&seg_dur={seg_len:.2f}")

    lines.append("#EXT-X-ENDLIST")
    resp = Response("\n".join(lines), status=200, content_type="application/vnd.apple.mpegurl")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/hls/segment.ts")
def hls_segment():
    """Generates an individual 6-second MPEG-TS video segment on the fly with FFmpeg."""
    url = request.args.get("url", "")
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"error": "Invalid URL"}), 400

    ss = request.args.get("ss", "0")
    seg_dur = request.args.get("seg_dur", request.args.get("t", "6"))
    try:
        ss_val = float(ss)
        dur_val = float(seg_dur)
    except ValueError:
        ss_val, dur_val = 0.0, 6.0

    headers_str = f"User-Agent: {api.ANDROID_USER_AGENT}\r\n"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-headers", headers_str,
        "-ss", str(ss_val),
        "-i", url,
        "-t", str(dur_val),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-sn",
        "-dn",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-threads", "0",
        "-pix_fmt", "yuv420p",
        "-crf", "28",
        "-c:a", "aac",
        "-b:a", "128k",
        "-f", "mpegts",
        "pipe:1"
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=20)
        if proc.returncode != 0:
            return jsonify({"error": "FFmpeg failed"}), 500

        resp = Response(proc.stdout, status=200, content_type="video/MP2T")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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

