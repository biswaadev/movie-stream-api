"""
Flask Movie App — proxies requests to the MovieBox upstream API and serves a
dark, Netflix-style frontend. The CDN streaming endpoint proxies Range
requests back to the browser so HTML5 video playback works despite the
CDN User-Agent block. Streams are served natively (HEVC/H.264); no
server-side transcoding.
"""

try:
    from gevent import monkey
    monkey.patch_all()
except Exception:
    pass

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
import re
import shutil
import subprocess
import time
import urllib.parse
from functools import wraps
import movie_api as api
import tracker

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ofc-movies-secret-key-2026")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "biswajittanti185@gmail.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "BISWA@9090")


# ---------------------------------------------------------------------------
# PWA Root Endpoints
# ---------------------------------------------------------------------------
@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    response = send_from_directory("static", "sw.js", mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


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
    user = request.form.get("username", "").strip().lower()
    pwd = request.form.get("password", "").strip()
    valid_users = [ADMIN_USERNAME.lower(), "biswajittanti185@gmail.com", "admin"]
    if user in valid_users and pwd == ADMIN_PASSWORD:
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
# ---------------------------------------------------------------------------
# Stream & Resource Token Encryption, Proxy & Response Sanitizer
# ---------------------------------------------------------------------------
import base64
from concurrent.futures import ThreadPoolExecutor

STREAM_SALT = "mb_stream_v2_salt_2026"

def encode_stream_token(raw_url: str) -> str:
    """Encrypt raw CDN URL into an opaque URL-safe token with no MovieBox keywords."""
    if not raw_url:
        return ""
    try:
        salt_bytes = STREAM_SALT.encode("utf-8")
        raw_bytes = raw_url.encode("utf-8")
        enc = bytes([b ^ salt_bytes[i % len(salt_bytes)] for i, b in enumerate(raw_bytes)])
        return base64.urlsafe_b64encode(enc).decode("utf-8").rstrip("=")
    except Exception:
        return ""

def decode_stream_token(token: str) -> str:
    """Decrypt URL-safe token back to the real upstream CDN URL."""
    if not token:
        return ""
    token = token.strip()
    if token.startswith("http://") or token.startswith("https://"):
        return token
    m = re.search(r'/(?:stream/v|dash|download/v|img/v|sub/v)/([^/?#.]+)', token)
    if m:
        token = m.group(1)
    try:
        for ext in (".mp4", ".m4s", ".jpg", ".jpeg", ".png", ".webp", ".vtt", ".mpd"):
            if token.endswith(ext):
                token = token[:-len(ext)]
                break
        pad = len(token) % 4
        if pad:
            token += "=" * (4 - pad)
        enc = base64.urlsafe_b64decode(token)
        salt_bytes = STREAM_SALT.encode("utf-8")
        dec = bytes([b ^ salt_bytes[i % len(salt_bytes)] for i, b in enumerate(enc)])
        url = dec.decode("utf-8")
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return ""
    except Exception:
        return ""

def is_dash_url(url: str) -> bool:
    return bool(url and (".mpd" in url or "__signCookie__" in url))

def parse_dash_url(url: str):
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    cookie = qs.get("__signCookie__", [""])[0]
    rep = qs.get("rep", ["0"])[0]
    raw_mpd = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return raw_mpd, cookie, rep

def sanitize_data(data):
    """Recursively scrub all raw CDN and upstream URLs/cookies, replacing with opaque tokens."""
    if isinstance(data, dict):
        out = {}
        # If this dict represents a video stream resource
        if "resourceLink" in data or "dashUrl" in data:
            raw_stream = data.get("resourceLink") or data.get("dashUrl") or data.get("url") or ""
            if raw_stream and isinstance(raw_stream, str) and raw_stream.startswith("http"):
                if data.get("signCookie") and "__signCookie__" not in raw_stream:
                    sep = "&" if "?" in raw_stream else "?"
                    raw_stream += f"{sep}__signCookie__={urllib.parse.quote(data['signCookie'])}"
                tok = encode_stream_token(raw_stream)
                is_dash = is_dash_url(raw_stream)
                s_url = f"/dash/{tok}/index.mpd" if is_dash else f"/stream/v/{tok}.mp4"
                out["token"] = tok
                out["isDash"] = is_dash
                out["streamUrl"] = s_url
                if "resourceLink" in data:
                    out["resourceLink"] = s_url
                if "dashUrl" in data:
                    out["dashUrl"] = s_url
                if "tran-audio" in raw_stream:
                    out["isTranAudio"] = True

        for k, v in data.items():
            if k in ("signCookie", "cookie", "raw", "resourceLink", "dashUrl"):
                continue
            if k in ("detailUrl", "playUrl") and isinstance(v, str) and any(d in v for d in ("movie-box", "aoneroom", "hakunaymatata")):
                continue
            if k in ("subPath", "captionUrl") and isinstance(v, str) and v.startswith("http"):
                tok = encode_stream_token(v)
                out[k] = f"/sub/v/{tok}.vtt"
                continue
            if k in ("url", "img", "cover", "avatar", "icon", "poster") and isinstance(v, str) and v.startswith("http"):
                tok = encode_stream_token(v)
                if any(ext in v.lower() for ext in (".jpg", ".jpeg", ".png", ".webp", "/image/")) or k in ("img", "cover", "avatar", "icon", "poster"):
                    w_hint = 120 if k in ("avatar", "icon") else 180
                    out[k] = f"/img/v/{tok}.webp?w={w_hint}&q=45"
                elif any(ext in v.lower() for ext in (".vtt", ".srt")):
                    out[k] = f"/sub/v/{tok}.vtt"
                else:
                    is_dash = is_dash_url(v)
                    s_url = f"/dash/{tok}/index.mpd" if is_dash else f"/stream/v/{tok}.mp4"
                    if "token" not in out:
                        out["token"] = tok
                    if "isDash" not in out:
                        out["isDash"] = is_dash
                    if "streamUrl" not in out:
                        out["streamUrl"] = s_url
                    out[k] = s_url
                    if "tran-audio" in v:
                        out["isTranAudio"] = True
                continue
            out[k] = sanitize_data(v)
        return out
    elif isinstance(data, list):
        return [sanitize_data(x) for x in data]
    elif isinstance(data, str):
        if data.startswith("http") and any(d in data for d in ("aoneroom.com", "hakunaymatata.com", "cloudfront.net", "movie-box.co")):
            tok = encode_stream_token(data)
            if any(ext in data.lower() for ext in [".jpg", ".jpeg", ".png", ".webp", "/image/"]):
                return f"/img/v/{tok}.webp?w=180&q=45"
            elif any(ext in data.lower() for ext in [".mpd", "__signcookie__"]):
                return f"/dash/{tok}/index.mpd"
            elif any(ext in data.lower() for ext in [".vtt", ".srt"]):
                return f"/sub/v/{tok}.vtt"
            else:
                return f"/stream/v/{tok}.mp4"
        return data
    else:
        return data

_image_cache = {}
_IMAGE_CACHE_MAX = 1200

def get_cached_image(key):
    entry = _image_cache.get(key)
    if entry and (time.time() - entry[0] < 86400):
        return entry[1]
    return None

def set_cached_image(key, val):
    if len(_image_cache) > _IMAGE_CACHE_MAX:
        keys = list(_image_cache.keys())[:250]
        for k in keys:
            _image_cache.pop(k, None)
    _image_cache[key] = (time.time(), val)


@app.route("/img/v/<path:token>")
@app.route("/img/<path:token>")
def image_proxy_token(token):
    url = decode_stream_token(token)
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"error": "Invalid image token"}), 400
    w = request.args.get("w", "").strip()
    q = request.args.get("q", "").strip()
    w_val = int(w) if w.isdigit() else 180
    q_val = int(q) if q.isdigit() else 45
    # Cap maximum width to 360 and quality to 55 to guarantee low data footprint
    w_val = min(max(w_val, 80), 360)
    q_val = min(max(q_val, 25), 55)

    if ("aoneroom.com" in url or "hakunaymatata.com" in url) and "x-oss-process" not in url:
        sep = "&" if "?" in url else "?"
        url += f"{sep}x-oss-process=image/resize,w_{w_val}/format,webp/quality,q_{q_val}"

    cache_key = f"img_low_v2_{token}_{w_val}_{q_val}"
    cached = get_cached_image(cache_key)
    if cached:
        data, content_type = cached
        resp = Response(data, status=200, content_type=content_type)
        resp.headers["Cache-Control"] = "public, max-age=86400, immutable"
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    try:
        r = api._session.get(url, headers={"User-Agent": api.ANDROID_USER_AGENT}, timeout=10)
        if r.status_code == 200:
            c_type = r.headers.get("Content-Type", "image/webp")
            set_cached_image(cache_key, (r.content, c_type))
            resp = Response(r.content, status=200, content_type=c_type)
            resp.headers["Cache-Control"] = "public, max-age=86400, immutable"
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
        return jsonify({"error": f"Upstream error {r.status_code}"}), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/image_proxy")
def image_proxy():
    """Proxy image assets (posters/avatars) to bypass CORS and hotlink protection."""
    tok = request.args.get("t") or request.args.get("token") or ""
    if tok:
        return image_proxy_token(tok)
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing image URL"}), 400
    token = encode_stream_token(url)
    return image_proxy_token(token)


@app.route("/sub/v/<path:token>")
def sub_proxy_token(token):
    url = decode_stream_token(token)
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"error": "Invalid subtitle token"}), 400
    try:
        r = api._session.get(url, headers={"User-Agent": api.ANDROID_USER_AGENT}, timeout=10)
        resp = Response(r.content, status=r.status_code, content_type="text/vtt; charset=utf-8")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 502


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
        return jsonify(sanitize_data(data))
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/suggest")
def suggest():
    keyword = request.args.get("q", "").strip()
    try:
        return jsonify(sanitize_data(api.search_suggest(keyword)))
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/detail/<subject_id>")
def detail(subject_id):
    try:
        return jsonify(sanitize_data(api.subject_detail(subject_id)))
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/seasons/<subject_id>")
def seasons(subject_id):
    try:
        return jsonify(sanitize_data(api.season_info(subject_id)))
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/token")
def get_stream_token():
    """Convert a raw CDN URL to an encrypted stream token."""
    raw_url = request.args.get("url", "").strip()
    token = encode_stream_token(raw_url)
    return jsonify({"token": token, "streamUrl": f"/stream/v/{token}.mp4" if token else ""})


@app.route("/api/resources_dubs")
def resources_dubs():
    """Fetch resources for every dub of a title concurrently. Returns sanitized stream tokens."""
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
                    raw_list = r.get("data", r).get("list", [])
                    entry["list"] = raw_list
                except Exception:  # noqa
                    pass
            return entry

        with ThreadPoolExecutor(max_workers=min(len(dubs), 8)) as executor:
            out = list(executor.map(fetch_dub, dubs))

        return jsonify(sanitize_data({"dubs": out}))
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
        data = api.get_resources(subject_id, se=se, ep=ep)
        return jsonify(sanitize_data(data))
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/subtitles")
def subtitles():
    """Extract subtitles/captions available for a title or episode with proxy tokens."""
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
                    raw_sub_url = c.get("url") or c.get("subPath") or c.get("captionUrl")
                    lang = c.get("language") or c.get("lanName") or c.get("lanCode") or "Subtitle"
                    if raw_sub_url and raw_sub_url not in seen:
                        seen.add(raw_sub_url)
                        sub_tok = encode_stream_token(raw_sub_url)
                        sub_list.append({"language": lang, "url": f"/sub/v/{sub_tok}.vtt"})
        return jsonify({"subtitles": sub_list})
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/download")
@app.route("/download/v/<path:token>")
def download(token=None):
    """Full download with Content-Disposition header and encrypted token support."""
    raw_token = token or request.args.get("t") or request.args.get("token") or request.args.get("url", "")
    url = decode_stream_token(raw_token)
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"error": "Invalid or expired download URL"}), 400
    title = request.args.get("title", "video")
    filename = title.replace("/", "_").replace('"', "")

    # Track download event
    ip = tracker.get_client_ip(request)
    tracker.log_activity(ip, "DOWNLOAD", media_title=title, details=f"filename={filename}", user_agent=request.headers.get("User-Agent", ""))

    if is_dash_url(url):
        raw_mpd, sign_cookie, rep = parse_dash_url(url)
        if shutil.which("ffmpeg"):
            headers_str = (
                f"User-Agent: ExoPlayerLib/2.19.1\r\n"
                f"Cookie: {sign_cookie}\r\n"
                f"Referer: https://www.movieboxpro.app/\r\n"
            )
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-headers", headers_str,
                "-i", raw_mpd,
                "-map", f"0:v:{rep}",
                "-map", "0:a:0?",
                "-c", "copy",
                "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                "-f", "mp4",
                "pipe:1",
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=64 * 1024)

            def generate_mp4():
                try:
                    while True:
                        chunk = proc.stdout.read(64 * 1024)
                        if not chunk:
                            break
                        yield chunk
                except GeneratorExit:
                    pass
                finally:
                    try:
                        proc.kill()
                    except Exception:
                        pass

            forwarded = Response(
                generate_mp4(),
                status=200,
                content_type="video/mp4",
            )
            forwarded.headers["Content-Disposition"] = f'attachment; filename="{filename}.mp4"'
            forwarded.headers["Access-Control-Allow-Origin"] = "*"
            forwarded.headers["Accept-Ranges"] = "bytes"
            return forwarded

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
        return jsonify(sanitize_data(api.trending()))
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/home")
def home():
    tab_id = request.args.get("tabId", "0")
    try:
        return jsonify(sanitize_data(api.home_feed(tab_id)))
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/related/<subject_id>")
def related(subject_id):
    try:
        return jsonify(sanitize_data(api.get_recommendations(subject_id)))
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# CDN video proxy — mandatory Android UA + Range forwarding + Token Decryption
# ---------------------------------------------------------------------------
@app.route("/stream")
@app.route("/stream/v/<path:token>")
def stream(token=None):
    raw_token = token or request.args.get("t") or request.args.get("token") or request.args.get("url", "")
    url = decode_stream_token(raw_token)
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"error": "Invalid or expired stream URL"}), 400

    if is_dash_url(url):
        raw_mpd, sign_cookie, rep = parse_dash_url(url)
        if shutil.which("ffmpeg"):
            headers_str = (
                f"User-Agent: ExoPlayerLib/2.19.1\r\n"
                f"Cookie: {sign_cookie}\r\n"
                f"Referer: https://www.movieboxpro.app/\r\n"
            )
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-headers", headers_str,
            ]
            ss = request.args.get("ss", "0").strip()
            try:
                ss_val = float(ss)
                if ss_val > 0:
                    cmd.extend(["-ss", str(ss_val)])
            except ValueError:
                pass

            cmd.extend([
                "-i", raw_mpd,
                "-map", f"0:v:{rep}",
                "-map", "0:a:0?",
                "-c", "copy",
                "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                "-f", "mp4",
                "pipe:1",
            ])
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=64 * 1024)

            def generate_stream():
                try:
                    while True:
                        chunk = proc.stdout.read(64 * 1024)
                        if not chunk:
                            break
                        yield chunk
                except GeneratorExit:
                    pass
                finally:
                    try:
                        proc.kill()
                    except Exception:
                        pass

            forwarded = Response(
                generate_stream(),
                status=200,
                content_type="video/mp4",
            )
            forwarded.headers["Access-Control-Allow-Origin"] = "*"
            forwarded.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            forwarded.headers["Access-Control-Allow-Headers"] = "Range, User-Agent"
            forwarded.headers["Accept-Ranges"] = "bytes"
            return forwarded
        else:
            return redirect(f"/dash/{raw_token}/index.mpd")

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


@app.route("/dash/<path:token>/index.mpd")
def dash_manifest(token):
    url = decode_stream_token(token)
    if not url:
        return jsonify({"error": "Invalid token"}), 400
    raw_mpd, sign_cookie, rep = parse_dash_url(url)

    # Parse CloudFront signed cookie components
    parts = {}
    for item in sign_cookie.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            parts[k] = v
    policy = parts.get("CloudFront-Policy", "")
    sig = parts.get("CloudFront-Signature", "")
    kpid = parts.get("CloudFront-Key-Pair-Id", "")

    headers = {
        "User-Agent": "ExoPlayerLib/2.19.1",
        "Cookie": sign_cookie,
        "Referer": "https://www.movieboxpro.app/",
    }
    try:
        r = api._session.get(raw_mpd, headers=headers, timeout=15)
        raw_text = r.text
        # Route all DASH chunks through local secure proxy route /dash/{token}/...
        # Zero external CDN URLs exposed to client or network tab!
        raw_text = raw_text.replace(
            'initialization="init-stream$RepresentationID$.m4s"',
            f'initialization="/dash/{token}/init-stream$RepresentationID$.m4s"'
        )
        raw_text = raw_text.replace(
            'media="chunk-stream$RepresentationID$-$Number%05d$.m4s"',
            f'media="/dash/{token}/chunk-stream$RepresentationID$-$Number%05d$.m4s"'
        )
        # Standardize MovieBox HEVC codec tag to RFC 6381 so browser MediaSource recognizes & decodes video track
        raw_text = raw_text.replace('codecs="hev1"', 'codecs="hvc1.1.6.L120.B0"')
        raw_text = raw_text.replace('codecs="hvc1"', 'codecs="hvc1.1.6.L120.B0"')

        resp = Response(raw_text, status=r.status_code, content_type="application/dash+xml")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/dash/<path:token>/<segment>")
def dash_segment(token, segment):
    url = decode_stream_token(token)
    if not url:
        return jsonify({"error": "Invalid token"}), 400
    raw_mpd, sign_cookie, _ = parse_dash_url(url)
    base_dash = raw_mpd.rsplit("/", 1)[0]
    seg_url = f"{base_dash}/{segment}"
    headers = {
        "User-Agent": "ExoPlayerLib/2.19.1",
        "Cookie": sign_cookie,
        "Referer": "https://www.movieboxpro.app/",
    }
    try:
        r = api._session.get(seg_url, headers=headers, stream=True, timeout=15)
        resp = Response(r.iter_content(chunk_size=128 * 1024), status=r.status_code, content_type="video/iso.segment")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 502


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
# Static assets
# ---------------------------------------------------------------------------
@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)

