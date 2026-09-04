# Video-on-Demand (VOD) Client API & Mobile Integration Specification

This technical specification details the client integration architecture, HMAC security protocol, catalog endpoints, and MPEG-DASH streaming lifecycle for developing native mobile clients (Android, iOS, Flutter, React Native).

---

## 1. System Architecture

The service utilizes a direct client-to-CDN architecture where catalog metadata is retrieved from the gateway service, while high-bitrate media playback is streamed directly from distributed CloudFront edge servers to the client.

```
┌─────────────────┐
│   Mobile App    │
└─┬─────────────┬─┘
  │             │
  │ (1) Metadata & Session Auth
  │             │
  ▼             ▼
┌───────────────┴───────────────┐        ┌───────────────────────────────┐
│      API Gateway Service      │        │      Global CloudFront CDN    │
│    (api6.aoneroom.com)        │        │   (sacdn.hakunaymatata.com)   │
└───────────────────────────────┘        └───────────────┬───────────────┘
                                                         │
                                        (2) Direct MPEG-DASH 1080p Video
                                                         │
                                                         ▼
                                                ┌─────────────────┐
                                                │ ExoPlayer / App │
                                                └─────────────────┘
```

* **Data Delivery:** High-bitrate video segments stream directly from CDN edge caches.
* **Stream Profiles:** Multi-resolution MPEG-DASH (1080p, 720p, 480p) supporting HEVC (H.265) and AVC (H.264).
* **Multi-Track Audio:** Discrete language dub tracks with timed text subtitle manifests.

---

## 2. Service Endpoints & Configuration

### Gateway Hosts

| Target | Host URL | Usage |
| :--- | :--- | :--- |
| **Primary Gateway** | `https://api6.aoneroom.com` | Primary API cluster |
| **Failover Gateway 1** | `https://api5.aoneroom.com` | Secondary failover host |
| **Failover Gateway 2** | `https://api4.aoneroom.com` | Tertiary failover host |

### Client Configuration Constants

```properties
CLIENT_SECRET_KEY = 76iRl07s0xSN9jqmEWAt79EBJZulIQIsV64FZr2O
CLIENT_APP_ID = com.community.oneroom
CLIENT_VERSION_CODE = 50020045
CLIENT_VERSION_NAME = 3.0.03.0529.03
```

### Standard Client User-Agent
```http
com.community.oneroom/50020045 (Linux; U; Android 13; en_US; 22101316G; Build/TQ2A.230405.003; Cronet/135.0.7012.3)
```

### Client Device Information (`X-Client-Info`)
Every request carries a stringified JSON header identifying the client environment:
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

## 3. Request Security & Signature Specification

All HTTP requests to the API gateway must be cryptographically signed with two headers: `X-Client-Token` and `x-tr-signature`.

### A. `X-Client-Token`
* **Input:** Unix epoch timestamp in seconds (integer, e.g. `1725375000`).
* **Calculation:** Reverse the digits of the timestamp, compute the **MD5** hex hash of the reversed string.
* **Output Format:** `"{timestamp},{md5_hex}"`
* *Example:*
  ```
  Timestamp: 1725375000
  Reversed:  0005735271
  MD5 Hash:  d41d8cd98f00b204e9800998ecf8427e
  Result:    1725375000,d41d8cd98f00b204e9800998ecf8427e
  ```

### B. `x-tr-signature` (HMAC-MD5)
The gateway validates request integrity using an HMAC-MD5 signature over a canonical representation of the request.

1. **Construct the Canonical String** using newline (`\n`) separated fields:
   ```text
   HTTP_METHOD
   ACCEPT_HEADER
   CONTENT_TYPE
   BODY_LENGTH
   TIMESTAMP
   BODY_MD5_HEX
   CANONICAL_URI
   ```
   * `HTTP_METHOD`: Uppercase method (`GET` or `POST`).
   * `ACCEPT_HEADER`: Value of Accept header (`application/json`).
   * `CONTENT_TYPE`: Value of Content-Type (`application/json` when body is present, otherwise empty).
   * `BODY_LENGTH`: Length of body in bytes as string (empty if no body).
   * `TIMESTAMP`: Unix timestamp in seconds (string).
   * `BODY_MD5_HEX`: MD5 hex hash of first 4096 bytes of body (empty if no body).
   * `CANONICAL_URI`: Pathname plus alphabetized/sorted query parameters (e.g., `/path?a=1&b=2`).

2. **Compute Signature:**
   * Decode `CLIENT_SECRET_KEY` from Base64 into binary key bytes.
   * Calculate **HMAC-MD5** of the canonical UTF-8 string using the key bytes.
   * Base64-encode the resulting HMAC digest.
3. **Format:** `"{timestamp}|2|{base64_hmac}"`

---

## 4. Session Bootstrap & Token Lifecycle

Prior to fetching catalog metadata, the client initializes an authenticated session token.

### Bootstrap Request
* **Method:** `GET`
* **Path:** `/wefeed-mobile-bff/tab-operating?page=1&tabId=0&version=`
* **Headers:** Include signed headers (`X-Client-Token`, `x-tr-signature`, `User-Agent`, `X-Client-Info`).

### Token Extraction
Extract the session object from the response header **`x-user`** (or `X-User`):
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI...",
  "uid": 12345678
}
```

### Authorization Header
Attach the session token to all subsequent requests:
```http
Authorization: Bearer {token}
```
*Note: If the gateway returns HTTP 401 Unauthorized, refresh the session by repeating the bootstrap step.*

---

## 5. API Catalog Endpoints

### 1. Home Page Feeds, Rankings & Category Carousels
Retrieves curated media carousels, home banners, trending titles, top rankings, and genre rows for the app's landing screen.

* **Method:** `GET`
* **Path:** `/wefeed-mobile-bff/tab-operating`
* **Query Parameters:**
  * `tabId`: Feed Category Identifier:
    * `0`: **Featured Home Feed** (Trending Now, Hollywood, Cinema, Bollywood, South Indian, Coming Soon)
    * `1`: **Movies Collection** (Hollywood Hits, Regional Cinema)
    * `2`: **Top Charts & Rankings** (Top Movies, Trending in Cinema, Sci-Fi Future, Super Hero, Action, Comedy)
    * `3`: **Kids & Learning**
  * `page`: Page index (default: `1`)
  * `version`: Version token (pass empty string `""` on first call; cache and pass the returned `version` string on subsequent calls)

* **Key Categories Returned in Home Feed (`tabId=0` & `tabId=2`):**
  * 🔥 **Trending Now**: Top viral titles updated daily
  * 🏆 **Top Movies (Rankings)**: Highest-ranked box-office titles (`tabId=2`)
  * 🎬 **Cinema / New Releases**: Latest theatrical releases
  * 🌐 **Hollywood**: English & dual-audio blockbusters
  * 🎭 **Bollywood & South Indian**: Hindi, Tamil, Telugu blockbusters
  * 🦸 **Super Hero & Sci-Fi Future**: Marvel, DC, and futuristic sci-fi
  * 📅 **Coming Soon**: Upcoming titles with posters and release dates

* **Response Sample (Home Feed Structure):**
  ```json
  {
    "code": 0,
    "data": {
      "tabId": 0,
      "version": "3bac7cb9205a921072c59be089a2d33c",
      "items": [
        {
          "type": "SUBJECTS_MOVIE",
          "title": "🔥Trending Now",
          "subjects": [
            {
              "subjectId": "5349301616099452064",
              "title": "Gandhari [Hindi]",
              "releaseDate": "2026-09-03",
              "cover": {
                "url": "https://pbcdn.aoneroom.com/image/2026/09/03/2c4018c2164acab9.jpg"
              }
            }
          ]
        },
        {
          "type": "SUBJECTS_MOVIE",
          "title": "Hollywood",
          "subjects": [
            {
              "subjectId": "7826893701690839800",
              "title": "The Runner [Hindi]",
              "releaseDate": "2026-09-02",
              "cover": {
                "url": "https://pbcdn.aoneroom.com/image/2026/08/27/cca59929eb029e9f.jpg"
              }
            }
          ]
        }
      ]
    }
  }
  ```

---

### 2. Anime & Genre Catalog Browsing
To browse Anime and specific genres in your mobile app, query the search endpoint with appropriate type filters:

* **Method:** `POST`
* **Path:** `/wefeed-mobile-bff/subject-api/search`
* **Browse Anime Series:**
  ```json
  {
    "keyword": "Anime",
    "page": 1,
    "perPage": 20,
    "subjectType": 2
  }
  ```
  *(subjectType: `2` filters exclusively for episodic series and anime).*

* **Browse Top Ranked Media:**
  ```json
  {
    "keyword": "Top",
    "page": 1,
    "perPage": 20,
    "subjectType": 0
  }
  ```

---

### 2. Search Media Catalog
Full-text query against titles, keywords, and release years.

* **Method:** `POST`
* **Path:** `/wefeed-mobile-bff/subject-api/search`
* **Request Body:**
  ```json
  {
    "keyword": "Avatar",
    "page": 1,
    "perPage": 20,
    "subjectType": 0
  }
  ```
  *(subjectType: `0` = All Media, `1` = Feature Films, `2` = Episodic Series)*

* **Response:**
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
          "cover": {
            "url": "https://pbcdn.aoneroom.com/image/2026/01/27/d0afc52be1b6f93d5c9a70f9105f3a37.jpg"
          }
        }
      ],
      "pager": { "hasMore": true, "totalCount": 100 }
    }
  }
  ```

---

### 3. Search Autocomplete Suggestions
* **Method:** `GET`
* **Path:** `/wefeed-mobile-bff/subject-api/search-suggest`
* **Query Parameters:**
  * `keyword`: Partial query prefix (e.g. `Avat`)
  * `perPage`: Result limit (e.g. `10`)

---

### 4. Media Title Details & Metadata
Retrieves full title metadata, synopsis, localized dub editions, and cast details.

* **Method:** `GET`
* **Path:** `/wefeed-mobile-bff/subject-api/get`
* **Query Parameters:**
  * `subjectId`: Media item identifier (e.g. `1654274595068805784`)

* **Response Structure:**
  ```json
  {
    "code": 0,
    "data": {
      "subjectId": "1654274595068805784",
      "title": "Avatar [Hindi]",
      "postTitle": "Avatar",
      "contentRating": "PG-13",
      "releaseDate": "2009-12-18",
      "durationSeconds": 9720,
      "description": "A paraplegic Marine dispatched to the moon Pandora on a unique mission becomes torn between following his orders and protecting the world he feels is his home.",
      "cover": {
        "url": "https://pbcdn.aoneroom.com/image/2026/01/27/d0afc52be1b6f93d5c9a70f9105f3a37.jpg",
        "width": 640,
        "height": 960
      },
      "stills": {
        "url": "https://pbcdn.aoneroom.com/image/2023/03/23/92f5465c28dfb684904e3bda68038194.jpg",
        "width": 1024,
        "height": 428
      },
      "dubs": [
        {
          "subjectId": "8906247916759695608",
          "lanName": "Original Audio",
          "lanCode": "en",
          "original": true
        },
        {
          "subjectId": "1654274595068805784",
          "lanName": "Hindi dub",
          "lanCode": "hi",
          "original": false
        },
        {
          "subjectId": "3387445081316775448",
          "lanName": "Tamil dub",
          "lanCode": "ta",
          "original": false
        },
        {
          "subjectId": "5331311270480462024",
          "lanName": "Telugu dub",
          "lanCode": "te",
          "original": false
        }
      ],
      "staffList": [
        {
          "staffId": "6788255266560690664",
          "name": "James Cameron",
          "character": "Director / Writer",
          "avatarUrl": "https://pbcdn.aoneroom.com/image/2022/11/15/cdb17ee9325004c3f10d07645bba5eee.jpg"
        },
        {
          "staffId": "3994348652033481080",
          "name": "Sam Worthington",
          "character": "Jake Sully",
          "avatarUrl": "https://pbcdn.aoneroom.com/image/2022/09/02/1f75434211ac1a206c2f6166ba7a89fd.jpg"
        },
        {
          "staffId": "7901170757754605944",
          "name": "Zoe Saldana",
          "character": "Neytiri",
          "avatarUrl": "https://pbcdn.aoneroom.com/image/2022/09/02/242ceae290dd9d08ccee8d3327616429.jpg"
        }
      ]
    }
  }
  ```

---

### 5. Episodic Series Seasons & Episode Index
* **Method:** `GET`
* **Path:** `/wefeed-mobile-bff/subject-api/season-info`
* **Query Parameters:**
  * `subjectId`: Series subject identifier (e.g. `102175217904222864`)
* **Response:**
  Returns available seasons list, episode numbers, episode titles, thumbnails, and runtimes.

---

### 6. Video Stream Manifest Resolver (`play-info`)
Resolves playback streams and secure CloudFront access credentials for a title.

* **Method:** `GET`
* **Path:** `/wefeed-mobile-bff/subject-api/play-info`
* **Query Parameters:**
  * `subjectId`: Media or dub item identifier
  * `se`: Season index (`0` for standalone movies, `1+` for series)
  * `ep`: Episode index (`0` for standalone movies, `1+` for series)

* **Response Structure:**
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

### 7. Related Media Recommendations ("More Like This")
Retrieves personalized similar movies, related series, and franchise sequels associated with a specific title for the details screen.

* **Method:** `POST`
* **Path:** `/wefeed-mobile-bff/subject-api/play-related-rec`
* **Request Body:**
  ```json
  {
    "subjectId": "1654274595068805784"
  }
  ```
* **Response Sample:**
  ```json
  {
    "code": 0,
    "data": {
      "items": [
        {
          "subjectId": "5286679345266918984",
          "subjectType": 1,
          "title": "Avengers: Infinity War [Hindi]",
          "releaseDate": "2018-04-27",
          "duration": "2h 29m",
          "genre": "Action, Adventure, Sci-Fi",
          "imdbRatingValue": "8.4",
          "cover": {
            "url": "https://pbcdn.aoneroom.com/image/2026/01/27/edd42613fb50bad2e5552966a0d6cba1.jpg"
          },
          "corner": "Hindi",
          "restrictKid": 0
        }
      ]
    }
  }
  ```

---

## 6. Manifest Parsing & Playback Integration

### Step 1: Extract Base Stream URL from `signCookie`
The `signCookie` contains an encoded `CloudFront-Policy` parameter. Decode the Base64 policy JSON to resolve the resource path:

```json
{
  "Statement": [
    {
      "Resource": "https://sacdn.hakunaymatata.com/dash/1654274595068805784_0_0_1080_h265_919/*"
    }
  ]
}
```

Remove trailing `/*` to get the base stream directory:
* **Base Directory:** `https://sacdn.hakunaymatata.com/dash/1654274595068805784_0_0_1080_h265_919`
* **Master Manifest (.mpd):** `https://sacdn.hakunaymatata.com/dash/1654274595068805784_0_0_1080_h265_919/index.mpd`

---

### Step 2: Native Android Playback (ExoPlayer / Media3)

ExoPlayer connects directly to CloudFront with the signed cookie:

```kotlin
import com.google.android.exoplayer2.MediaItem
import com.google.android.exoplayer2.SimpleExoPlayer
import com.google.android.exoplayer2.source.dash.DashMediaSource
import com.google.android.exoplayer2.upstream.DefaultHttpDataSource

fun playStream(manifestUrl: String, signCookie: String) {
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
}
```

---

### Step 3: Flutter Playback (`better_player`)

```dart
import 'package:better_player/better_player.dart';

void initializePlayer(String manifestUrl, String signCookie) {
  BetterPlayerDataSource dataSource = BetterPlayerDataSource(
    BetterPlayerDataSourceType.network,
    manifestUrl,
    headers: {
      "Cookie": signCookie,
      "Referer": "https://www.movieboxpro.app/",
      "User-Agent": "ExoPlayerLib/2.19.1"
    },
    videoFormat: BetterPlayerVideoFormat.dash,
  );

  betterPlayerController.setupDataSource(dataSource);
}
```

---

### Step 4: Parameter-Signed Media URLs (Query Parameter Authentication)

For players where HTTP headers cannot be configured, extract the three security attributes from `signCookie`:
* `Policy`: from `CloudFront-Policy`
* `Signature`: from `CloudFront-Signature`
* `Key-Pair-Id`: from `CloudFront-Key-Pair-Id`

Query-authenticated format:
```text
{Base_URL}/index.mpd?Policy={Policy}&Signature={Signature}&Key-Pair-Id={Key-Pair-Id}
```

---

## 7. Subtitles & Closed Captions
* **Method:** `GET`
* **Path:** `/wefeed-mobile-bff/subject-api/caption`
* **Query Parameters:**
  * `subjectId`: Title identifier
  * `se`: Season number (`0` for movies)
  * `ep`: Episode number (`0` for movies)
* **Response:**
  Returns subtitle tracks array with language codes (`en`, `hi`, `es`, `fr`, etc.) and direct `.srt` / `.vtt` asset URLs.
