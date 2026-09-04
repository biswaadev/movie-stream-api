# OFC Movies — Complete API & Client Integration Specification

This technical specification details the complete API architecture for the **OFC Movies Platform**. It covers both the **Shielded Application Backend API** (which conceals all upstream CDN and API endpoints from client apps) and the **Upstream Provider Gateway Specification** (for low-level protocol reference and native mobile integration).

---

## Table of Contents
1. [Architecture Overview](#1-architecture-overview)
2. [Section A: Shielded Application Backend API](#2-section-a-shielded-application-backend-api)
   - [Authentication & Token Security](#a-token-encryption--stream-shielding)
   - [Catalog & Discovery Endpoints](#b-catalog--discovery-endpoints)
   - [Media Resource & Stream Endpoints](#c-media-resource--stream-endpoints)
   - [Secure Asset & Proxy Endpoints](#d-secure-asset--proxy-endpoints)
   - [Admin & Analytics Endpoints](#e-admin--analytics-endpoints)
3. [Section B: Client Player Integration Guide](#3-section-b-client-player-integration-guide)
   - [Web Player (DASH.js / HLS)](#a-web-player-integration)
   - [Native Android (ExoPlayer / Media3)](#b-native-android-exoplayer--media3)
   - [Flutter (better_player / video_player)](#c-flutter-integration)
   - [External Players (VLC & MX Player Intents)](#d-external-players-vlc--mx-player)
4. [Section C: Direct Upstream Provider Gateway Specification](#4-section-c-direct-upstream-provider-gateway-specification)
   - [Gateway Hosts & Client Headers](#gateway-hosts--client-headers)
   - [HMAC-MD5 Request Signing Algorithm](#hmac-md5-request-signing-algorithm)
   - [Session Bootstrap & Lifecycle](#session-bootstrap--lifecycle)
   - [Upstream Endpoints Reference](#upstream-endpoints-reference)

---

# 1. Architecture Overview

To provide fast, reliable media playback and protect upstream CDN credentials from public exposure in browser DevTools and network inspection tools, the platform uses a **Shielded Gateway Architecture**:

```
┌────────────────────────────────────────────────────────┐
│      Client Applications (Web / Android / iOS)         │
└───────────────────────────┬────────────────────────────┘
                            │
                            │ All requests use opaque tokens (No CDN URLs exposed)
                            ▼
┌────────────────────────────────────────────────────────┐
│           OFC Movies Application Backend               │
│               (Flask + Gunicorn + Cache)               │
├───────────────────────────┬────────────────────────────┤
│ • JSON Sanitizer Engine   │ • Token Cipher (XOR Salt)  │
│ • DASH Manifest Shield    │ • WebP Image Compressor    │
│ • Segment Proxy Worker    │ • Stream Range Forwarder   │
└─────────────┬────────────────────────────┬─────────────┘
              │                            │
  (1) Signed API Requests      (2) Segment Streaming (ExoPlayer UA)
              │                            │
              ▼                            ▼
┌───────────────────────────┐┌───────────────────────────┐
│  Upstream BFF Gateway     ││   CloudFront / Ali CDN    │
│   (api6.aoneroom.com)     ││ (sacdn.hakunaymatata.com) │
└───────────────────────────┘└───────────────────────────┘
```

### Key Security & Performance Guarantees:
* **Zero Upstream CDN Leaks:** External domains (`*.aoneroom.com`, `*.hakunaymatata.com`, `*.cloudfront.net`, `movie-box.co`) and raw CloudFront cookies are completely scrubbed from all API responses.
* **Lightweight WebP Images:** Images are automatically resized to mobile-optimized WebP (~7 KB vs 2.8 MB original) via the proxy.
* **Byte-Range Seeking:** The `/stream/v/{token}.mp4` proxy injects required Android User-Agents and forwards HTTP `206 Partial Content` ranges for instant video seeking.
* **Manifest Chunk Masking:** MPEG-DASH manifests route initialization and media segments through `/dash/{token}/{segment}` proxy paths so player network activity remains 100% on the application domain.

---

# 2. Section A: Shielded Application Backend API

Base URL: `https://<your-domain>` (or `http://127.0.0.1:5000` locally).

---

### A. Token Encryption & Stream Shielding

All stream URLs, media segments, download links, images, and subtitles are referenced via URL-safe encrypted tokens.

#### Cipher Specification:
* **Algorithm:** Symmetric byte-level XOR with cyclic salt key.
* **Salt Key:** `mb_stream_v2_salt_2026`
* **Encoding:** URL-safe Base64 (padding stripped: `+` replaced with `-`, `/` with `_`, trailing `=` removed).

#### Python Reference Implementation:
```python
import base64

STREAM_SALT = "mb_stream_v2_salt_2026"

def encode_stream_token(url: str) -> str:
    salt_bytes = STREAM_SALT.encode("utf-8")
    url_bytes = url.encode("utf-8")
    enc = bytes([b ^ salt_bytes[i % len(salt_bytes)] for i, b in enumerate(url_bytes)])
    return base64.urlsafe_b64encode(enc).decode("utf-8").rstrip("=")

def decode_stream_token(token: str) -> str:
    # Strip extensions if present (.mp4, .m4s, .webp, etc.)
    for ext in (".mp4", ".m4s", ".jpg", ".png", ".webp", ".vtt", ".mpd"):
        if token.endswith(ext):
            token = token[:-len(ext)]
            break
    pad = len(token) % 4
    if pad:
        token += "=" * (4 - pad)
    enc = base64.urlsafe_b64decode(token)
    salt_bytes = STREAM_SALT.encode("utf-8")
    return bytes([b ^ salt_bytes[i % len(salt_bytes)] for i, b in enumerate(enc)]).decode("utf-8")
```

---

### B. Catalog & Discovery Endpoints

#### 1. Home Feed
Returns categorized rows of trending movies, Hollywood hits, cinema releases, and collections.

* **Method:** `GET`
* **Path:** `/api/home`
* **Query Parameters:**
  * `tabId` (optional): Feed tab (`0`: Featured Home, `1`: Movies, `2`: Top Charts & Rankings, `3`: Kids). Default: `0`.
* **Sample Response:**
  ```json
  {
    "tabId": 0,
    "items": [
      {
        "type": "SUBJECTS_MOVIE",
        "title": "🔥Trending Now",
        "subjects": [
          {
            "subjectId": "8282836313385190960",
            "title": "JoJo's Bizarre Adventure [Hindi]",
            "releaseDate": "2012-10-04",
            "imdbRatingValue": "8.5",
            "corner": "Hindi",
            "description": "The story of the Joestar family...",
            "cover": {
              "url": "/img/v/BRYrAwdISk4dPRVWMV0AAxo6QF9dW0MBMB5bGwgACjpZ...webp?w=180&q=45",
              "width": 750,
              "height": 1071
            }
          }
        ]
      }
    ]
  }
  ```

#### 2. Daily Trending
Fetches the top viral and trending titles across all categories.

* **Method:** `GET`
* **Path:** `/api/trending`
* **Sample Response:**
  ```json
  {
    "data": {
      "items": [
        {
          "subjectId": "5349301616099452064",
          "title": "Gandhari [Hindi]",
          "releaseDate": "2026-09-03",
          "imdbRatingValue": "6.4",
          "genre": "Action, Drama, Thriller",
          "description": "Follows a mother's relentless pursuit...",
          "cover": {
            "url": "/img/v/BRYrAwdISk4dPRVWMV0AA...webp?w=180&q=45"
          }
        }
      ]
    }
  }
  ```

#### 3. Search Catalog
Full-text search across movies and TV series.

* **Method:** `GET`
* **Path:** `/api/search`
* **Query Parameters:**
  * `q` (required): Search keyword (e.g. `Avatar`)
  * `page` (optional): Page number (default: `1`)
  * `type` (optional): Media filter (`0`: All, `1`: Movies only, `2`: Series/Anime only). Default: `0`.
* **Sample Response:**
  ```json
  {
    "items": [
      {
        "id": "1654274595068805784",
        "title": "Avatar",
        "subjectType": 1,
        "year": "2009",
        "rating": "7.9",
        "img": "/img/v/BRYrAwdISk4d...webp?w=180&q=45",
        "description": "A paraplegic Marine dispatched to the moon Pandora..."
      }
    ],
    "pager": { "hasMore": true, "totalCount": 100 }
  }
  ```

#### 4. Search Autocomplete Suggestions
Fast typeahead search suggestion tags.

* **Method:** `GET`
* **Path:** `/api/suggest`
* **Query Parameters:**
  * `q` (required): Query prefix (e.g. `avat`)
* **Sample Response:**
  ```json
  {
    "data": [
      { "id": "1654274595068805784", "title": "Avatar" },
      { "id": "6739928172911293812", "title": "Avatar: The Way of Water" }
    ]
  }
  ```

---

### C. Media Resource & Stream Endpoints

#### 1. Title Details & Dubs Metadata
Returns full synopsis, localized audio dub editions, cast, ratings, and runtime.

* **Method:** `GET`
* **Path:** `/api/detail/{subjectId}`
* **Sample Response:**
  ```json
  {
    "subjectId": "1654274595068805784",
    "title": "Avatar [Hindi]",
    "durationSeconds": 9720,
    "description": "A paraplegic Marine dispatched to the moon Pandora...",
    "cover": {
      "url": "/img/v/BRYrAwdISk4d...webp?w=180&q=45"
    },
    "dubs": [
      { "subjectId": "8906247916759695608", "lanName": "Original Audio", "original": true },
      { "subjectId": "1654274595068805784", "lanName": "Hindi dub", "original": false },
      { "subjectId": "3387445081316775448", "lanName": "Tamil dub", "original": false }
    ],
    "staffList": [
      {
        "name": "James Cameron",
        "character": "Director",
        "avatarUrl": "/img/v/BRYr...webp?w=120&q=45"
      }
    ]
  }
  ```

#### 2. Series Seasons & Episodes Index
Returns the list of seasons and available episodes for series.

* **Method:** `GET`
* **Path:** `/api/seasons/{subjectId}`
* **Sample Response:**
  ```json
  {
    "seasons": [
      { "se": 0, "title": "Season 1", "maxEp": 24 },
      { "se": 1, "title": "Season 2", "maxEp": 12 }
    ]
  }
  ```

#### 3. Available Streams & Resolutions
Retrieves available resolutions (1080p, 720p, 480p, HEVC, H.264) for a title or episode, returning shielded `/stream/v/...` or `/dash/...` endpoints.

* **Method:** `GET`
* **Path:** `/api/resources`
* **Query Parameters:**
  * `subjectId` (required): Media identifier
  * `se` (optional): Season index (`0` for movie, `1+` for TV series). Default: `0`.
  * `ep` (optional): Episode index (`0` for movie, `1+` for TV series). Default: `0`.
* **Sample Response:**
  ```json
  {
    "data": {
      "list": [
        {
          "resolution": 1080,
          "codecName": "hevc",
          "size": 361030181,
          "duration": 1422,
          "isDash": true,
          "token": "BRYrAwdISk4ePhVWMV0JDR8qXFFLWwwWP...",
          "streamUrl": "/dash/BRYrAwdISk4ePhVWMV0JDR8qXFFLWwwWP.../index.mpd"
        },
        {
          "resolution": 720,
          "codecName": "h264",
          "size": 245103991,
          "duration": 1422,
          "isDash": false,
          "token": "BRYrAwdISk4ePhVWMV0JDR8qXFFLWwwWQ...",
          "streamUrl": "/stream/v/BRYrAwdISk4ePhVWMV0JDR8qXFFLWwwWQ....mp4"
        }
      ]
    }
  }
  ```

#### 4. Multi-Dub Concurrent Streams (`resources_dubs`)
Concurrently resolves available video streams for every dubbed language track of the title.

* **Method:** `GET`
* **Path:** `/api/resources_dubs`
* **Query Parameters:**
  * `subjectId` (required): Media identifier
  * `se` (optional): Season index. Default: `0`.
  * `ep` (optional): Episode index. Default: `0`.
* **Sample Response:**
  ```json
  {
    "dubs": [
      {
        "subjectId": "8906247916759695608",
        "lanName": "Original Audio",
        "list": [
          { "resolution": 1080, "streamUrl": "/dash/TOKEN_1/index.mpd", "token": "TOKEN_1" }
        ]
      },
      {
        "subjectId": "1654274595068805784",
        "lanName": "Hindi dub",
        "list": [
          { "resolution": 1080, "streamUrl": "/dash/TOKEN_2/index.mpd", "token": "TOKEN_2" }
        ]
      }
    ]
  }
  ```

#### 5. Subtitles & Closed Captions
Extracts timed-text subtitle tracks for a title or episode.

* **Method:** `GET`
* **Path:** `/api/subtitles`
* **Query Parameters:**
  * `subjectId` (required): Media identifier
  * `se` (optional): Season index. Default: `0`.
  * `ep` (optional): Episode index. Default: `0`.
* **Sample Response:**
  ```json
  {
    "subtitles": [
      { "language": "English", "url": "/sub/v/TOKEN_EN.vtt" },
      { "language": "Hindi", "url": "/sub/v/TOKEN_HI.vtt" },
      { "language": "Spanish", "url": "/sub/v/TOKEN_ES.vtt" }
    ]
  }
  ```

#### 6. Related Media Recommendations
* **Method:** `GET`
* **Path:** `/api/related/{subjectId}`
* **Response:** Array of recommended similar titles with posters, ratings, and release years.

---

### D. Secure Asset & Proxy Endpoints

#### 1. DASH Master Manifest Proxy
Delivers the `.mpd` manifest with representation chunks rewritten to point exclusively to the local backend proxy.

* **Method:** `GET`
* **Path:** `/dash/{token}/index.mpd`
* **Headers:** Standard browser headers.
* **Behavior:** Proxies upstream manifest, replaces initialization/media chunk paths with `/dash/{token}/init-stream...` and `/dash/{token}/chunk-stream...`. Sets `Content-Type: application/dash+xml` and `Access-Control-Allow-Origin: *`.

#### 2. DASH Media Segment Proxy
Streams `.m4s` initialization and media segments from upstream CloudFront using the securely decoded cookie.

* **Method:** `GET`
* **Path:** `/dash/{token}/{segment}`
* **Parameters:** `segment` e.g. `init-stream0.m4s` or `chunk-stream0-00001.m4s`.
* **Behavior:** Streams segment bytes with `Content-Type: video/iso.segment`.

#### 3. Direct MP4 Stream Proxy (HTTP Range Seeking)
Proxies MP4 video streams while injecting the required upstream Android User-Agent.

* **Method:** `GET`
* **Path:** `/stream/v/{token}.mp4`
* **Headers:** Supports `Range: bytes=start-end` header.
* **Behavior:** Returns `206 Partial Content` with `Content-Range` and `Accept-Ranges: bytes`.

#### 4. Lightweight WebP Image Proxy
Fetches, compresses, and caches image posters and avatars.

* **Method:** `GET`
* **Path:** `/img/v/{token}.webp`
* **Query Parameters:**
  * `w` (optional): Max width in pixels (default: `180`, capped between `80` and `360`).
  * `q` (optional): WebP quality factor (default: `45`, capped between `25` and `55`).
* **Cache Header:** `Cache-Control: public, max-age=86400, immutable`.
* **Average File Size:** ~7 KB to 10 KB per poster.

#### 5. Subtitle WebVTT Proxy
* **Method:** `GET`
* **Path:** `/sub/v/{token}.vtt`
* **Behavior:** Streams `.vtt` file with `Content-Type: text/vtt; charset=utf-8`.

#### 6. Video Download Endpoint
Serves the full video with `Content-Disposition: attachment` headers for saving to disk.

* **Method:** `GET`
* **Path:** `/download/v/{token}.mp4?title={filename}`
* **Behavior:**
  * If source is an MP4: Streams with `Content-Disposition: attachment; filename="{filename}.mp4"`.
  * If source is DASH: Uses server-side `ffmpeg` to remux segments into a continuous MP4 file on-the-fly (`-c copy`).

---

### E. Admin & Analytics Endpoints

*(Requires session login at `/admin`)*

* `GET /api/admin/stats`: Returns server uptime, total video plays, active users, and downloads.
* `GET /api/admin/logs?page=1&limit=30&action=STREAM`: Paginated activity log entries with client IP, media title, timestamp, and user-agent.
* `POST /api/admin/clear_logs`: Purges historical visitor activity logs.

---

# 3. Section B: Client Player Integration Guide

Client applications should consume the **Shielded Backend API** directly. Below are working examples for major player runtimes:

### A. Web Player Integration

Using standard [dash.js](https://github.com/Dash-Industry-Forum/dash.js):

```html
<script src="https://cdn.dashjs.org/latest/dash.all.min.js"></script>
<video id="videoPlayer" controls autoplay style="width:100%; aspect-ratio:16/9;"></video>

<script>
  async function loadVideo(subjectId) {
    const res = await fetch(`/api/resources?subjectId=${subjectId}`);
    const data = await res.json();
    const stream = data.data.list[0];

    const player = dashjs.MediaPlayer().create();
    // Direct playback through shielded local proxy
    player.initialize(document.querySelector("#videoPlayer"), stream.streamUrl, true);
  }
</script>
```

---

### B. Native Android (ExoPlayer / Media3)

For an Android application consuming our shielded proxy endpoints, no custom cookie management is required on the mobile client:

```kotlin
import android.net.Uri
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.exoplayer.ExoPlayer

fun playMovie(player: ExoPlayer, streamUrl: String, isDash: Boolean) {
    val mediaItem = if (isDash) {
        MediaItem.Builder()
            .setUri(Uri.parse(streamUrl)) // e.g. "https://your-domain.com/dash/{token}/index.mpd"
            .setMimeType(MimeTypes.APPLICATION_MPD)
            .build()
    } else {
        MediaItem.Builder()
            .setUri(Uri.parse(streamUrl)) // e.g. "https://your-domain.com/stream/v/{token}.mp4"
            .setMimeType(MimeTypes.VIDEO_MP4)
            .build()
    }

    player.setMediaItem(mediaItem)
    player.prepare()
    player.play()
}
```

---

### C. Flutter Integration

```dart
import 'package:better_player/better_player.dart';

void playStream(String streamUrl, bool isDash) {
  BetterPlayerDataSource dataSource = BetterPlayerDataSource(
    BetterPlayerDataSourceType.network,
    streamUrl,
    videoFormat: isDash ? BetterPlayerVideoFormat.dash : BetterPlayerVideoFormat.other,
  );

  betterPlayerController.setupDataSource(dataSource);
}
```

---

### D. External Players (VLC & MX Player)

On Android, launch external media players directly via Android Intents using the absolute stream URL:

```javascript
// VLC Player Intent
const vlcIntent = `intent:${absoluteStreamUrl}#Intent;package=org.videolan.vlc;type=video/*;end`;
window.location.href = vlcIntent;

// MX Player Intent
const mxIntent = `intent:${absoluteStreamUrl}#Intent;package=com.mxtech.videoplayer.ad;type=video/*;end`;
window.location.href = mxIntent;
```

---

# 4. Section C: Direct Upstream Provider Gateway Specification

This section documents the underlying upstream BFF gateway protocol (`api6.aoneroom.com`) for architecture reference.

### Gateway Hosts & Client Headers

| Gateway Host | Environment |
| :--- | :--- |
| `https://api6.aoneroom.com` | Primary Gateway |
| `https://api5.aoneroom.com` | Secondary Failover |
| `https://api4.aoneroom.com` | Tertiary Failover |

#### Mandatory Headers:
```http
User-Agent: com.community.oneroom/50020045 (Linux; U; Android 13; en_US; 22101316G; Build/TQ2A.230405.003; Cronet/135.0.7012.3)
Accept: application/json
Content-Type: application/json
X-Client-Token: {timestamp},{md5_hex_reversed_timestamp}
x-tr-signature: {timestamp}|2|{base64_hmac_md5}
X-Client-Info: {"package_name":"com.community.oneroom","version_code":50020045,"os":"android","os_version":"13"}
```

---

### HMAC-MD5 Request Signing Algorithm

All upstream requests must compute `x-tr-signature` over the canonical representation of the request:

```text
HTTP_METHOD
ACCEPT_HEADER
CONTENT_TYPE
BODY_LENGTH
TIMESTAMP
BODY_MD5_HEX
CANONICAL_URI
```

1. **Secret Key:** `76iRl07s0xSN9jqmEWAt79EBJZulIQIsV64FZr2O` (Base64-decoded into raw binary bytes).
2. **Hash:** Compute HMAC-MD5 of the UTF-8 canonical string.
3. **Format:** Output is formatted as `"{timestamp}|2|{base64_hmac}"`.

---

### Session Bootstrap & Lifecycle

Before calling upstream endpoints, retrieve a guest session token:

1. Send `GET /wefeed-mobile-bff/tab-operating?page=1&tabId=0&version=` with signed headers.
2. Extract user token from the response header **`x-user`**:
   ```json
   {
     "token": "eyJhbGciOiJIUzI1NiIsInR5cCI...",
     "uid": 12345678
   }
   ```
3. Attach to all subsequent requests as:
   ```http
   Authorization: Bearer {token}
   ```

---

### Upstream Endpoints Reference

| Upstream Path | Method | Description |
| :--- | :--- | :--- |
| `/wefeed-mobile-bff/tab-operating` | `GET` | Home feed categories, carousels, and rankings (`tabId=0,1,2,3`) |
| `/wefeed-mobile-bff/subject-api/search` | `POST` | Full catalog search (`keyword`, `page`, `perPage`, `subjectType`) |
| `/wefeed-mobile-bff/subject-api/search-suggest` | `GET` | Autocomplete suggestions (`keyword`, `perPage`) |
| `/wefeed-mobile-bff/subject-api/get` | `GET` | Media detail metadata and audio dub editions (`subjectId`) |
| `/wefeed-mobile-bff/subject-api/season-info` | `GET` | Episodic series seasons & episode numbers (`subjectId`) |
| `/wefeed-mobile-bff/subject-api/play-info` | `GET` | Resolves raw streams and CloudFront `signCookie` |
| `/wefeed-mobile-bff/subject-api/play-related-rec` | `POST` | Recommendations / "More Like This" (`subjectId`) |
| `/wefeed-mobile-bff/subject-api/caption` | `GET` | Subtitle track manifests (`subjectId`, `se`, `ep`) |
