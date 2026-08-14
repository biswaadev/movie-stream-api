# Dub / resource structure findings

- `/api/resources` list items have: title (e.g. "Avatar [Hindi]-720P", "Avatar-480P-h265"), resolution, codecName, size, uploadBy, sourceUrl, resourceLink.
- The resource `title` itself contains the dub marker like "[Hindi]" — best per-quality dub indicator.
- `/api/detail` has `dubs` list: [{lanCode, lanName, original, subjectId, type}] e.g. Original Audio (en), Hindi dub (hi), Tamil dub, Telugu dub... This describes available dubs for the whole title, with each dub's own subjectId.
- Search result items have `corner` field (e.g. "Hindi") and `language`.
- Plan: show "720P · Hindi dub" style labels from resource.title; add detail header line with dubs; download button using /download endpoint (same proxy, returns 200 full stream with Content-Disposition attachment).
