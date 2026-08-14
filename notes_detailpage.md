# Detail page (/watch/<id>) test notes

Verified working:
- Detail page loads: poster, title, meta tags, desc, dub strip, Watch Now section with video, Quality menu button, Play/Download buttons, Seasons & Episodes (Avatar series shows S2/S3 tabs + E1-E20 grid).
- Quality menu opens with checkmark + size meta; selecting item updates player src and qualityBtnLabel.
- Play works: video played (readyState 4, currentTime advancing) via /stream proxy.
- Language group hidden when single lang ("Original") — correct.
- Download button enabled after playback, data-url set.

Bug found (to fix):
- Avatar: The Way of Water [Hindi] (subjectId 2109495361273219144) — boot preloaded resources se=0/ep=0, got 2 items, both lang key "Hindi". Quality menu renders 2 items correctly (360P, 1080P) but Language menu is EMPTY and languageGroup stays display:none.
- Root cause hypothesis: in renderControlMenus(), keys.length > 1 check — keys = ["Hindi"] so display:none is set, and lBtn is only set in the else branch ("Hindi"). But lBtn check earlier showed "—" then "Original"? Earlier console showed items [] and group display none. Actually the else branch sets lBtn.textContent to keys[0] = "Hindi". But earlier languageBtnLabel showed "Original"... that was the Avatar series page (single Original lang).
- For the Hindi page: expected behavior = Language button shows "Hindi", group hidden (only one lang available). That IS correct! So maybe NOT a bug: when only Hindi streams exist, Language control stays hidden but label "Hindi" should show. Verify what languageBtnLabel actually shows on Hindi page (was "—" in first check because boot set it '—' then renderControlMenus... recheck).
- Also: Play button on Hindi page initially says ▶ Play but resources ARE preloaded; startPlayback should just selectRes(0,true). But clicking Play earlier triggered "⏳ Fetching stream..." — on the series page (Avatar) because preloaded se=0/ep=0 list existed there too... actually on series page fetchResources ran, meaning resList was empty at boot for Avatar. That is because Avatar (series) preloaded resources may have returned list but then pickSeason reloaded.
- TODO: re-check languageBtnLabel on Hindi page after rendering.

IDs: Avatar series = 3109059618463516080; Avatar: The Way of Water [Hindi] = 2109495361273219144.

Final verification (browser + curl):
The detail page works end-to-end. On Avatar: The Way of Water [Hindi] (subjectId 2109495361273219144), the Language button correctly displays "Hindi" and stays hidden (single language) while the Quality menu lists both 360P and 1080P; switching to 1080P updates the player source and the download button (server log confirms 200 on /download with title "Avatar: The Way of Water [Hindi]"). The Quality menu shows checkmarks, codecs, and sizes. On the Avatar series page, season tabs (S2/S3) and episode grids render, and /api/resources for se=1 ep=3 returns 10 items with valid links; the /stream Range request returns 206 with a proper Content-Range header (44.6 MB file), so episode playback seeks correctly. The earlier "browser not available" error was transient; curl verified the same flows.

## Language selector strategy (new feature)

Each dub in detail.dubs has its own subjectId. Fetching /api/resources with a dub's subjectId returns that dub's streams (verified: Telugu subjectId 1412546275970635256 returns "Avatar: The Way of Water | 1080" = Telugu dub, Hindi subjectId = current page). So the Language selector can fetch all dubs' resource lists and switch the player to the chosen dub's streams. For movies pass se=0&ep=0; for series the dub subjectId should match the current se/ep (subjectIds are per-title, series share episode numbering). Plan: add /api/resources_dubs endpoint returning {lanName, subjectId, list} per dub; frontend renders Language menu like Quality menu; switching re-fetches player src.

## Language selector implementation (2nd revision — DONE editing)

Implementation complete in detail.html + app.py. The /api/resources_dubs endpoint returns per-dub resource lists using each dub's own subjectId (verified: Original=5583598183634063680 with 1 stream, Hindi=2109495361273219144 with 2 streams [360P/1080P], Telugu=1412546275970635256 with 1 stream, esla=4557133657210863936 with 1 [720P], ptbr=6842678337450955232 with 3 streams). Frontend state.dubLists holds [{lanName, lanCode, subjectId, list}]; renderLanguageMenu() always builds a Language menu like the Quality menu (checkmark, 🌐 name, option count), shows the Language control whenever dubs.length > 1, and selectLang(i) swaps resList to the chosen dub and autoplay-switches the player. refreshDubLists(se,ep) re-fetches dub lists when changing episodes so the selector stays valid per episode. Old title-based language logic removed. renderLanguageMenu() is called from render(), renderControlMenus(), and refreshDubLists().

Remaining testing: browser unavailable intermittently — will test via browser after reset on /watch/2109495361273219144. Verify: Language button visible with "Hindi" label, menu lists Original/Hindi/Telugu/esla/Portuguese with checkmark, clicking Telugu switches player to Telugu stream (should autoplay), download updates. Then repackage zip and deliver.
Public preview URL: https://5000-ieb4a0zivputg90hkt18u-3c951276.sg1.manus.computer/
Test IDs: Avatar series=3109059618463516080; Avatar: The Way of Water [Hindi]=2109495361273219144.

## Language selector implementation (FINAL VERIFICATION)

Verified via Playwright:
- Page loads correctly on Avatar: The Way of Water [Hindi].
- Initial language label is 'Hindi', group display is 'block'.
- Language menu items populated: Original Audio (1 option), Hindi (2 options), Telugu (1 option), esla (1 option), ptbr (3 options).
- Switching to Telugu:
  - Label updates to 'Telugu'.
  - Player src updates to the Telugu stream URL.
  - Quality selector reloads (Telugu only has 1080P).
- Implementation is solid. All dubs are now selectable via a proper in-player menu.
