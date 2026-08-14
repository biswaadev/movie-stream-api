# Flask MovieBox

A Flask movie app that proxies the upstream MovieBox API (`api6.aoneroom.com`) server-side, implementing the full request-signing flow described in `DIRECT_API_USAGE_GUIDE.md`. Because browsers cannot call the upstream API directly (CORS + CDN User-Agent blocks), this Flask server performs all signed requests and relays the CDN video stream to an HTML5 player.

## Project layout

```
flask_movie_app/
├── app.py            # Flask routes: search, suggest, detail, seasons, resources, /stream proxy
├── movie_api.py      # Signed API client (X-Client-Token, x-tr-signature, X-Client-Info, Bearer JWT)
├── templates/
│   └── index.html    # Single-page dark UI: search, poster grid, detail modal, video player
├── static/
├── server.log
└── README.md
```

## How it implements the guide

The client in `movie_api.py` follows the guide exactly. Every request is signed with an `X-Client-Token` (reversed timestamp + MD5) and an `x-tr-signature` (HMAC-MD5 over a canonical request string with the base64-decoded secret `76iRl07s0xSN9jqmEWAt79EBJZulIQIsV64FZr2O`), and carries the Android `X-Client-Info` blob and `User-Agent`. Before the first call, the server bootstraps a JWT bearer token from the `x-user` header of the tab-operating endpoint; tokens are automatically refreshed on 401 responses. Requests fail over from `api6` to `api5`/`api4` when a host errors.

The `/stream` endpoint proxies `bcdn.hakunaymatata.com` CDN URLs, injecting the mandatory Android User-Agent (a plain browser call would get 403) and forwarding `Range` headers so the HTML5 `<video>` element receives proper `206 Partial Content` responses for seekable playback.

| Frontend action | Route used | Upstream endpoint |
|---|---|---|
| Search | `/api/search?q=&type=&page=` | `POST /wefeed-mobile-bff/subject-api/search` |
| Detail | `/api/detail/<id>` | `GET .../subject-api/get?subjectId=` |
| Seasons | `/api/seasons/<id>` | `GET .../subject-api/season-info?subjectId=` |
| Streams | `/api/resources?subjectId=&se=&ep=` | `GET .../subject-api/resource` |
| Playback | `/stream?url=` | CDN `resourceLink` (proxied) |

## Running locally

```bash
pip3 install flask requests
cd flask_movie_app
python3 app.py
# open http://localhost:5000
```

The server listens on `0.0.0.0:5000` in debug mode. Search supports the subject-type filter (0 = All, 1 = Movies, 2 = Series, 5 = Anime) with 20 results per page, and series open a season/episode picker (episodes capped at 100 per season for UI sanity).

## Notes and caveats

CDN resource links carry an expiry timestamp (`t=`) of roughly 30 minutes; if playback fails with a 403, re-open the detail modal to fetch a fresh link. The app is a technical demonstration of the API client; streaming copyrighted content may violate the upstream service's terms of use and local copyright law, so use at your own discretion.
