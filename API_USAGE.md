# MovieBox / OneRoom Complete API Documentation (Mobile App Guide)

This document provides the complete technical specification for integrating MovieBox/OneRoom APIs directly into an Android (Kotlin/Java), Flutter, iOS (Swift), or React Native mobile application **without requiring any intermediate backend server**.

---

## 1. System Overview & Direct Architecture

```
[ Your Mobile App ] 
       │
       ├─── (1) Bootstrap & Token ─────► api6.aoneroom.com
       │
       ├─── (2) Search, Detail, Streams ► api6.aoneroom.com
       │
       └─── (3) Direct 1080p Video ────► Amazon CloudFront (sacdn.hakunaymatata.com)
```

* **Server Cost:** $0 (Zero backend servers needed)
* **Video Bandwidth:** 100% streamed directly from Amazon CloudFront CDN to user's device
* **Quality:** Up to 4K / 1080p HEVC (H.265) & H.264 with Multi-Audio Dubs & Subtitles

---

## 2. Base URLs & Constants

| Key | Value |
| :--- | :--- |
| **Primary Host** | `https://api6.aoneroom.com` |
| **Fallback Host 1** | `https://api5.aoneroom.com` |
| **Fallback Host 2** | `https://api4.aoneroom.com` |
| **Secret Key (Base64)** | `76iRl07s0xSN9jqmEWAt79EBJZulIQIsV64FZr2O` |
| **Version Code** | `50020045` |
| **App Package** | `com.community.oneroom` |

### Required Android User-Agent:
```http
com.community.oneroom/50020045 (Linux; U; Android 13; en_US; 22101316G; Build/TQ2A.230405.003; Cronet/135.0.7012.3)
```

### Required Client Info Header (`X-Client-Info`):
JSON stringified payload:
```json
{
  "package_name": "com.community.oneroom",
  "version_name": "3.0.03.0529.03",
  "version_code": 50020045,
  "os": "android",
  "os_version": "13",
  "install_ch": "ps",
  "device_id": "0123456789abcdef0123456789abcdef",
  "install_store": "ps",
  "gaid": "38400000-8cf0-11bd-b23e-10b96e4ef00d",
  "brand": "Redmi",
  "model": "22101316G",
  "system_language": "en",
  "net": "NETWORK_WIFI",
  "region": "US",
  "timezone": "America/New_York",
  "sp_code": "40401",
  "X-Play-Mode": "2"
}
```

---

## 3. Cryptographic Signature Generation

Every HTTP request must include two security headers: `X-Client-Token` and `x-tr-signature`.

### A. `X-Client-Token` Algorithm
1. Take current Unix epoch timestamp in seconds (integer, e.g. `1725375000`).
2. Reverse the timestamp string (e.g. `"0005735271"`).
3. Compute **MD5 hex digest** of the reversed string.
4. Format: `"{timestamp},{md5_hex}"`.

### B. `x-tr-signature` Algorithm (HMAC-MD5)
1. Construct the Canonical String using newline (`\n`) separated fields:
   ```text
   HTTP_METHOD (e.g. GET or POST)
   ACCEPT_HEADER (usually application/json)
   CONTENT_TYPE (application/json if body present, else empty)
   BODY_LENGTH (length of body in bytes as string, else empty)
   TIMESTAMP (Unix epoch in seconds as string)
   BODY_MD5_HEX (MD5 hex of first 4096 bytes of body, else empty)
   CANONICAL_URL (pathname + sorted query string e.g. /path?a=1&b=2)
   ```
2. Decode `SECRET_KEY_DEFAULT` from Base64 into raw bytes.
3. Compute **HMAC-MD5** of the Canonical String using the raw secret bytes.
4. Base64-encode the resulting HMAC digest.
5. Format: `"{timestamp}|2|{base64_hmac}"`.

---

## 4. App Lifecycle & Bootstrap Protocol

Before making authenticated requests, your app must obtain a temporary user session Bearer token.

### Step 1: Bootstrap Call
* **Method:** `GET`
* **Path:** `/wefeed-mobile-bff/tab-operating?page=1&tabId=0&version=`
* **Headers:** Send signed headers (`X-Client-Token`, `x-tr-signature`, `User-Agent`, `X-Client-Info`).

### Step 2: Extract Bearer Token
Check the response headers for **`x-user`** (or `X-User`):
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsIn...",
  "uid": 12345678
}
```
Store this token in memory.

### Step 3: Attach to Subsequent Requests
For all following requests, include the header:
```http
Authorization: Bearer {token}
```
*(If any API returns HTTP 401 Unauthorized, re-run the bootstrap call to refresh the token).*

---

## 5. Endpoints Reference

### 1. Home Feed / Banners / Trending
* **Method:** `GET`
* **Path:** `/wefeed-mobile-bff/tab-operating`
* **Query Params:**
  * `tabId`: `0` (Home), `1` (Movies), `2` (TV Series), `3` (Anime)
  * `page`: `1`
  * `version`: `""`
* **Response:**
  Returns carousels, trending banners, recommendation rows with `subjectId`, `title`, `cover`, `rating`.

---

### 2. Search Titles
* **Method:** `POST`
* **Path:** `/wefeed-mobile-bff/subject-api/search`
* **Body:**
  ```json
  {
    "keyword": "Avatar",
    "page": 1,
    "perPage": 20,
    "subjectType": 0
  }
  ```
  *(subjectType: 0 = All, 1 = Movies, 2 = TV Shows)*
* **Response Sample:**
  ```json
  {
    "code": 0,
    "data": {
      "items": [
        {
          "subjectId": "1654274595068805784",
          "title": "Avatar",
          "subjectType": 1,
          "releaseDate": "2009-12-18",
          "imdbRating": "7.9",
          "cover": { "url": "https://macdn.aoneroom.com/..." }
        }
      ],
      "pager": { "hasMore": true, "totalCount": 100 }
    }
  }
  ```

---

### 3. Search Suggestions (Auto-Complete)
* **Method:** `GET`
* **Path:** `/wefeed-mobile-bff/subject-api/search-suggest`
* **Query Params:**
  * `keyword`: `Avat`
  * `perPage`: `10`

---

### 4. Subject Details (Full Movie / Series Info)
* **Method:** `GET`
* **Path:** `/wefeed-mobile-bff/subject-api/get`
* **Query Params:**
  * `subjectId`: `1654274595068805784`
* **Response Contains:**
  * `title`, `description`, `duration`, `genres`, `releaseDate`
  * `dubs`: Array of audio dubs (e.g. Hindi, Tamil, Telugu, English) with their unique `subjectId`
  * `actors` & `directors`: Cast & Crew with photos and IDs
  * `seasons`: Array of seasons for TV series

---

### 5. TV Series Season & Episode List
* **Method:** `GET`
* **Path:** `/wefeed-mobile-bff/subject-api/season-info`
* **Query Params:**
  * `subjectId`: `102175217904222864` (Series subjectId)
* **Response Contains:**
  * List of seasons (`se: 1, 2, ...`)
  * Episodes array per season (`ep: 1, 2, ...`) with episode thumbnails, titles, and runtimes.

---

### 6. Official Video Streams Resolver (`play-info`)
> ⚠️ **CRITICAL NOTE:** Do NOT call `/subject-api/resource` because direct MP4s on that endpoint return dummy 917 KB "Please Update the App" promotional video clips. Always use `/subject-api/play-info`.

* **Method:** `GET`
* **Path:** `/wefeed-mobile-bff/subject-api/play-info`
* **Query Params:**
  * `subjectId`: `{subjectId}` (Use specific dub subjectId if dub is chosen)
  * `se`: `0` (or Season number e.g. `1`)
  * `ep`: `0` (or Episode number e.g. `1`)
* **Response Sample:**
  ```json
  {
    "code": 0,
    "data": {
      "streams": [
        {
          "codecName": "hevc",
          "duration": 10689,
          "resolutions": "1080,720,480",
          "size": 4980781430,
          "signCookie": "CloudFront-Policy=eyJTdGF0ZW1lbnQiOlt7IlJlc291cmNlIjoiaHR0cHM6Ly9zYWNkbi5oYWt1bmF5bWF0YXRhLmNvbS9kYXNoLzE2NTQyNzQ1OTUwNjg4MDU3ODRfMF8wXzEwODBfaDI2NV85MTkvKiIsIkNvbmRpdGlvbiI6eyJEYXRlTGVzc1RoYW4iOnsiQVdTOkVwb2NoVGltZSI6MTc4OTA1MjU3Mn19fV19;CloudFront-Signature=...;CloudFront-Key-Pair-Id=KMHN1LQ1HEUPL;"
        }
      ]
    }
  }
  ```

---

## 6. How to Extract the Direct Video Stream

### Step 1: Parse the CDN Base URL from `signCookie`
Decode the `CloudFront-Policy` base64 string inside `signCookie`:
```python
policy_b64 = cookie.split("CloudFront-Policy=")[1].split(";")[0]
# URL-safe base64 decode:
raw_json = base64.urlsafe_b64decode(padded_b64).decode("utf-8")
data = json.loads(clean_json)
base_dash_url = data["Statement"][0]["Resource"].replace("/*", "")
# Example: https://sacdn.hakunaymatata.com/dash/1654274595068805784_0_0_1080_h265_919
```

### Step 2: MPEG-DASH Manifest URL
The master manifest URL is:
```text
{base_dash_url}/index.mpd
```

---

## 7. Video Playback in Mobile Apps (Zero Server Bandwidth)

### Approach A: Android ExoPlayer (Recommended for Android APK)
ExoPlayer supports native DASH streaming and signed CloudFront cookies with **zero proxy servers**:

```kotlin
import com.google.android.exoplayer2.MediaItem
import com.google.android.exoplayer2.SimpleExoPlayer
import com.google.android.exoplayer2.source.dash.DashMediaSource
import com.google.android.exoplayer2.upstream.DefaultHttpDataSource

val manifestUrl = "https://sacdn.hakunaymatata.com/dash/.../index.mpd"
val signCookie = stream.signCookie

// Attach CloudFront cookie to ExoPlayer network layer
val dataSourceFactory = DefaultHttpDataSource.Factory()
    .setUserAgent("ExoPlayerLib/2.19.1")
    .setDefaultRequestProperties(mapOf(
        "Cookie" to signCookie,
        "Referer" to "https://www.movieboxpro.app/"
    ))

val mediaSource = DashMediaSource.Factory(dataSourceFactory)
    .createMediaSource(MediaItem.fromUri(manifestUrl))

player.setMediaSource(mediaSource)
player.prepare()
player.play()
```

---

### Approach B: Flutter (`video_player` / `better_player`)
```dart
import 'package:better_player/better_player.dart';

BetterPlayerDataSource dataSource = BetterPlayerDataSource(
  BetterPlayerDataSourceType.network,
  "https://sacdn.hakunaymatata.com/dash/.../index.mpd",
  headers: {
    "Cookie": signCookie,
    "Referer": "https://www.movieboxpro.app/",
    "User-Agent": "ExoPlayerLib/2.19.1"
  },
  videoFormat: BetterPlayerVideoFormat.dash,
);

betterPlayerController.setupDataSource(dataSource);
```

---

### Approach C: Signed Query Parameters (Direct CDN Without Cookie Header)
Amazon CloudFront also supports URL query parameters instead of headers:
Extract from `signCookie`:
* `Policy`: value of `CloudFront-Policy`
* `Signature`: value of `CloudFront-Signature`
* `Key-Pair-Id`: value of `CloudFront-Key-Pair-Id`

Attach as query parameters:
```text
https://sacdn.hakunaymatata.com/dash/.../chunk-00001.m4s?Policy={Policy}&Signature={Signature}&Key-Pair-Id={Key-Pair-Id}
```
This URL is publicly fetchable from any device without authentication headers.

---

## 8. Subtitles / Captions
* **Method:** `GET`
* **Path:** `/wefeed-mobile-bff/subject-api/caption`
* **Query Params:**
  * `subjectId`: `{subjectId}`
  * `se`: `0`
  * `ep`: `0`
* **Response:**
  List of subtitle tracks with language code (`en`, `hi`, `es`, `fr`, etc.) and direct `.srt` / `.vtt` CDN links.
