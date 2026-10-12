# Demonstration videos downloaded from YouTube

## Status

`accepted`

**Decision Date:** 2026-10-12

## Context

After a session the athlete wrote "I don't know if I was doing it right" about the knee plank and
asked for a YouTube video for each exercise. The lock screen is a Wayland session lock: no browser
can be opened while it is up, and the machine may be offline.

## Decision

- **Videos are downloaded into the exercise library** (`<library>/<id>/video.mp4`, 480p at most,
  a few MB each) with yt-dlp, by `sportlock library videos`. They play in the lock screen with
  QtMultimedia, muted and looping, from the local file.
- **The video is chosen by rules, not by a model**: yt-dlp's top 8 search results for
  "<name> exercise how to", keeping videos from 10 s to 6 min whose title names the exercise (75 %
  of the words of its name or an alias); then a teaching word in the title ("how to", "form"…), a
  20 s–3 min length and the search rank decide. No match means no video: a wrong video teaches the
  wrong movement.
- **The video's source lives in its own `video.json`**, next to the file, so rebuilding the
  instructions (`library build --force`) never loses it, and the source is shown under the video.
- The lock screen shows the video instead of the picture for exercises done fewer than 3 times
  (like the full instructions); **V** switches.

## Consequences

### Positive

- Every exercise with a good video can be watched during the session, offline.
- The choice is deterministic and unit-tested.

### Negative

- About 300 MB of videos in the library.
- Generic entries (e.g. cool-down stretching) get no video.
- A video's title can name the exercise and still show a variation; `library videos <id> --force`
  searches again, and deleting `video.json` removes one.

## Files

- `sportlock/exercises/domain/videos.py`: `VideoCandidate`, `choose_video`
- `sportlock/exercises/infrastructure/youtube_videos.py`: search, download, `video.json`
- `sportlock/exercises/infrastructure/details_repository.py`: reads `video.json`
- `sportlock/locks/ui/lock_screen/shell.qml`: the video player
