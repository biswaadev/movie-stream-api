# Download + dub label test notes

Verified: /download HEAD returns 200, Content-Disposition attachment filename="Avatar.mp4", Content-Length 397007736. Modal shows dub strip (Original Audio / Hindi dub / Telugu dub / esla / ptbr). Quality chips show "360P Hindi · 626 MB · H.264" and "1080P Hindi · 3.33 GB · H.264".

Bug found: clicking 1080P chip (chips[1].click()) left dlBtn text as "⬇ Download 360P (626 MB)" — because pickRes(1) threw `TypeError: Cannot read properties of undefined (reading '1')` at line 490. Cause: pickRes reads `window.__resList[i]` but the modal was re-rendered; window.__resList was likely set in startPlay... Actually error at pickRes line `window.__resList[i]` means __resList undefined. But earlier render ran pickRes(0) via startPlay which sets nothing... Wait — renderDownloadBtn works, so startPlay ran; but window.__resList should be set in loadRes BEFORE startPlay... loadRes sets window.__resList then startPlay uses it for render; startPlay at 467-470 falls through without setting? It does `window.__resList` access at 507? No. The __resList is set at line ~457 `window.__resList = list;` then startPlay reads it. So __resList should exist. The error suggests startPlay ran `window.__resList = list;` AFTER the chips render — actually renderDownloadBtn at end of startPlay ran fine, so __resList existed. Maybe console error came from stale context (page re-rendered between calls). Investigate.

## Final verification (after fix)
- Fixed: window.__resList now set in startPlay so chip switching updates dlBtn. Verified: chips[1] (1080P Hindi) click -> dlBtn "⬇ Download 1080P (3.33 GB)", active chip updated, __resList length 2.
- Series test (Naruto) still works; download button applies S/episode suffix to filename.
- Preview URL: https://5000-ieb4a0zivputg90hkt18u-3c951276.sg1.manus.computer/
- Zip: /home/ubuntu/flask_movie_app.zip
