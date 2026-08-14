"""
MovieBox upstream API client implementing the DIRECT_API_USAGE_GUIDE.
All requests are signed with X-Client-Token / x-tr-signature / X-Client-Info,
use the Android User-Agent, and carry the Bearer token obtained from the
`x-user` header of the bootstrap request.
"""

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE_URL = "https://api6.aoneroom.com"
FALLBACK_URLS = ["https://api5.aoneroom.com", "https://api4.aoneroom.com"]

SECRET_KEY_DEFAULT = "76iRl07s0xSN9jqmEWAt79EBJZulIQIsV64FZr2O"
VERSION_CODE = 50020045

ANDROID_USER_AGENT = (
    f"com.community.oneroom/{VERSION_CODE} "
    "(Linux; U; Android 13; en_US; 22101316G; Build/TQ2A.230405.003; Cronet/135.0.7012.3)"
)

DEVICE_ID = uuid.uuid4().hex
GAID = str(uuid.uuid4())

ANDROID_CLIENT_INFO = json.dumps(
    {
        "package_name": "com.community.oneroom",
        "version_name": "3.0.03.0529.03",
        "version_code": VERSION_CODE,
        "os": "android",
        "os_version": "13",
        "install_ch": "ps",
        "device_id": DEVICE_ID,
        "install_store": "ps",
        "gaid": GAID,
        "brand": "Redmi",
        "model": "22101316G",
        "system_language": "en",
        "net": "NETWORK_WIFI",
        "region": "US",
        "timezone": "America/New_York",
        "sp_code": "40401",
        "X-Play-Mode": "2",
    }
)

_session = requests.Session()

# Caches
_resources_cache = {}
_resources_cache_ttl = 60

_api_cache = {}
_api_cache_ttl = 300  # 5 minutes for general API requests


def _get_cached(cache_dict, key, ttl):
    hit = cache_dict.get(key)
    if hit and (time.time() - hit[1]) < ttl:
        return hit[0]
    return None


def _set_cached(cache_dict, key, val):
    cache_dict[key] = (val, time.time())


def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def generate_x_client_token(ts: int) -> str:
    reversed_ts = str(ts)[::-1]
    h = md5_hex(reversed_ts.encode("utf-8"))
    return f"{ts},{h}"


def sorted_query_string(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query_params.sort(key=lambda x: x[0])
    return urllib.parse.urlencode(query_params, doseq=True)


def generate_x_tr_signature(method, accept, content_type, url, body, ts):
    parsed = urllib.parse.urlparse(url)
    path = parsed.path
    query = sorted_query_string(url)
    canonical_url = f"{path}?{query}" if query else path

    body_hash = ""
    body_length = ""
    if body is not None:
        body_bytes = body.encode("utf-8")
        body_hash = md5_hex(body_bytes[:4096])
        body_length = str(len(body_bytes))

    canonical_parts = [
        method.upper(),
        accept or "",
        content_type or "",
        body_length,
        str(ts),
        body_hash,
        canonical_url,
    ]
    canonical_string = "\n".join(canonical_parts)
    secret_bytes = base64.b64decode(SECRET_KEY_DEFAULT)
    mac = hmac.new(secret_bytes, canonical_string.encode("utf-8"), hashlib.md5).digest()
    return f"{ts}|2|{base64.b64encode(mac).decode('utf-8')}"


def build_signed_headers(method, url, body=None, auth_token=None):
    ts = int(time.time() * 1000)
    accept = "application/json"
    content_type = "application/json"
    headers = {
        "User-Agent": ANDROID_USER_AGENT,
        "Accept": accept,
        "Content-Type": content_type,
        "Connection": "keep-alive",
        "X-Client-Token": generate_x_client_token(ts),
        "x-tr-signature": generate_x_tr_signature(method, accept, content_type, url, body, ts),
        "X-Client-Info": ANDROID_CLIENT_INFO,
        "X-Client-Status": "0",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    return headers


# ---------------------------------------------------------------------------
# Token bootstrap (thread-safe, renews on 401/expiry)
# ---------------------------------------------------------------------------
class TokenStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._token = None

    def get(self, refresh=False):
        with self._lock:
            if self._token and not refresh:
                return self._token
            token = self._bootstrap()
            self._token = token
            return token

    def _bootstrap(self):
        url = (
            f"{BASE_URL}/wefeed-mobile-bff/tab-operating"
            "?page=1&tabId=0&version="
        )
        last_err = None
        for base in [BASE_URL] + FALLBACK_URLS:
            u = url.replace(BASE_URL, base)
            headers = build_signed_headers("GET", u)
            try:
                resp = _session.get(u, headers=headers, timeout=15)
                if resp.status_code == 401:
                    continue  # expired; try bootstrap again below
                x_user = resp.headers.get("x-user") or resp.headers.get("X-User")
                if x_user:
                    data = json.loads(x_user)
                    token = data.get("token")
                    if token:
                        return token
                if resp.status_code == 200:
                    continue
            except Exception as e:  # noqa
                last_err = e
        raise RuntimeError(f"Token bootstrap failed. Last error: {last_err}")


token_store = TokenStore()


def signed_request(method, path, params=None, body=None):
    """Make a signed request trying the primary host and fallbacks."""
    query = urllib.parse.urlencode(params) if params else ""
    last_err = None
    for base in [BASE_URL] + FALLBACK_URLS:
        url = f"{base}{path}?{query}".rstrip("?") if query else f"{base}{path}"
        body_str = json.dumps(body) if body is not None else None
        token = token_store.get()
        headers = build_signed_headers(method, url, body=body_str, auth_token=token)
        try:
            if method.upper() == "GET":
                resp = _session.get(url, headers=headers, timeout=20)
            else:
                resp = _session.post(url, headers=headers, data=body_str, timeout=20)
        except Exception as e:  # noqa
            last_err = e
            continue

        if resp.status_code == 401:
            token = token_store.get(refresh=True)
            headers = build_signed_headers(method, url, body=body_str, auth_token=token)
            try:
                if method.upper() == "GET":
                    resp = _session.get(url, headers=headers, timeout=20)
                else:
                    resp = _session.post(url, headers=headers, data=body_str, timeout=20)
            except Exception as e:  # noqa
                last_err = e
                continue

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                return {"raw": resp.text[:2000]}
        last_err = RuntimeError(f"{base} returned HTTP {resp.status_code}")

    raise last_err if last_err else RuntimeError("All hosts failed")


# ---------------------------------------------------------------------------
# High-level API functions
# ---------------------------------------------------------------------------
def search(keyword, page=1, per_page=20, subject_type=0):
    body = {"keyword": keyword, "page": page, "perPage": min(int(per_page), 20), "subjectType": int(subject_type)}
    return signed_request("POST", "/wefeed-mobile-bff/subject-api/search", body=body)


def search_suggest(keyword, per_page=10):
    cache_key = f"suggest_{keyword}_{per_page}"
    cached = _get_cached(_api_cache, cache_key, _api_cache_ttl)
    if cached:
        return cached
    res = signed_request("GET", "/wefeed-mobile-bff/subject-api/search-suggest", params={"keyword": keyword, "perPage": int(per_page)})
    _set_cached(_api_cache, cache_key, res)
    return res


def subject_detail(subject_id):
    cache_key = f"detail_{subject_id}"
    cached = _get_cached(_api_cache, cache_key, _api_cache_ttl)
    if cached:
        return cached
    res = signed_request("GET", "/wefeed-mobile-bff/subject-api/get", params={"subjectId": subject_id})
    _set_cached(_api_cache, cache_key, res)
    return res


def season_info(subject_id):
    cache_key = f"season_{subject_id}"
    cached = _get_cached(_api_cache, cache_key, _api_cache_ttl)
    if cached:
        return cached
    res = signed_request("GET", "/wefeed-mobile-bff/subject-api/season-info", params={"subjectId": subject_id})
    _set_cached(_api_cache, cache_key, res)
    return res


def get_resources(subject_id, se=0, ep=0):
    """Return resources for the given subject, season and episode.
    Fetches all pages and resolution passes in parallel and merges locally.
    If the requested subjectId returns only audio-only (`tran-audio`) tracks,
    it automatically falls back to fetching the original title's video stream
    so the video player receives a valid video track.
    """
    key = str(subject_id)
    hit = _get_cached(_resources_cache, key, _resources_cache_ttl)
    if hit:
        full = hit
    else:
        full = _fetch_all_resources(subject_id)
        _set_cached(_resources_cache, key, full)

    se_i, ep_i = int(se), int(ep)
    if se_i == 0 and ep_i == 0:
        filtered = full
    else:
        filtered = [x for x in full
                    if int(x.get("se", -1)) == se_i and int(x.get("ep", -1)) == ep_i]

    # Check if all filtered streams are audio-only (tran-audio)
    has_video = any('/tran-audio/' not in str(x.get("resourceLink", "")) for x in filtered)
    if not has_video and filtered:
        try:
            detail = subject_detail(subject_id)
            d = detail.get("data", detail)
            dubs = d.get("dubs", [])
            orig_dub = next((x for x in dubs if x.get("original")), None)
            if orig_dub and str(orig_dub.get("subjectId")) != str(subject_id):
                orig_res = get_resources(orig_dub.get("subjectId"), se=se, ep=ep)
                orig_list = orig_res.get("data", {}).get("list", [])
                video_items = [x for x in orig_list if '/tran-audio/' not in str(x.get("resourceLink", ""))]
                if video_items:
                    filtered = video_items + filtered
        except Exception:
            pass

    return {"data": {"list": filtered}, "pager": {"totalCount": len(filtered)}}


def _fetch_all_resources(subject_id, max_pages=40):
    """Parallel fetch of resource list across all resolution passes."""
    merged = []
    seen = set()
    lock = threading.Lock()

    def fetch_pass(resolution):
        for page in range(1, max_pages + 1):
            params = {"subjectId": subject_id, "se": "0", "ep": "0", "page": str(page)}
            if resolution is not None:
                params["resolution"] = str(resolution)
            try:
                item = signed_request("GET", "/wefeed-mobile-bff/subject-api/resource", params=params)
                data = item.get("data", item) or {}
                lst = data.get("list") or []
                if not lst:
                    break
                added = False
                for x in lst:
                    rid = x.get("resourceId") or x.get("postId") or x.get("resourceLink")
                    with lock:
                        if rid and rid in seen:
                            continue
                        if rid:
                            seen.add(rid)
                        merged.append(x)
                        added = True
                pager = data.get("pager") or {}
                if not pager.get("hasMore") or not added:
                    break
            except Exception:
                break

    passes = [None, "360", "480", "720", "1080", "2160"]
    with ThreadPoolExecutor(max_workers=len(passes)) as executor:
        futures = [executor.submit(fetch_pass, r) for r in passes]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception:
                pass

    return merged


def trending():
    """Fetch trending/recommended content."""
    cache_key = "trending"
    cached = _get_cached(_api_cache, cache_key, 600)
    if cached:
        return cached

    url = "https://h5-api.aoneroom.com/h5/subject-api/recommend"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    try:
        resp = _session.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            res = resp.json()
            d = res.get("data", res) or {}
            lst = d.get("items") or d.get("list") or d.get("verticalRanks") or []
            if lst:
                _set_cached(_api_cache, cache_key, res)
                return res
    except Exception:
        pass

    try:
        res = search("Hindi")
        _set_cached(_api_cache, cache_key, res)
        return res
    except Exception:
        return {"data": {"verticalRanks": []}}

