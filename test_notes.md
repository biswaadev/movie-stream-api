# Test Notes — Flask MovieBox

## Verified working
- Home page: 200
- /api/search?q=spider-man&type=1 -> code 0, data.items has 20 items
  - Item structure: subjectId, title, subjectType (1=movie,2=series,5=anime), cover.url, genre, duration, imdbRatingValue, language, countryName, releaseDate
- /api/detail/<id> -> code 0, description, cover.url, genre, duration
- /api/seasons/<id> -> data.seasons [{se, maxEp, resolutions}]
- /api/resources?subjectId=&se=&ep= -> data.list [{resolution, codecName, resourceLink (bcdn.hakunaymatata.com)}]
- /stream?url= with Range header -> 206 video/mp4, valid MP4 bytes (ISO Media)

## Issues found in UI screenshot
- Poster images not loading in grid (pbcdn.aoneroom.com likely blocked from sandbox browser? or lazy load issue) — data verified to return cover.url.
  - Actually images appear to have failed loading (lazy + error hides them). Need to check pbcdn accessibility.
- Fix: preload poster via img tag (loading=eager test), or proxy posters too.

## Verified in browser
- Spider-Man movie modal: poster, meta, desc render; Play fetched 360/720/1080 resources and video played via /stream proxy (readyState 4, duration 10689s).
- Fixed: isSeries false-positive hidden; season buttons filter maxEp>0.
- Naruto series: seasons S1/S2 rendered, S2 -> 220 episodes capped at 100, E1 loaded -> video playing (duration 1417s, currentTime advancing). All 360p quality chips (only 360 available for this episode).
- Images load fine; latency ~3s for search (upstream).

## TODO
- Test pbcdn.aoneroom.com image accessibility from sandbox.
- Series flow: seasons maxEp=220 for naruto, ep-select capped at 100.
