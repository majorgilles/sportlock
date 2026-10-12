from sportlock.exercises.domain.videos import VideoCandidate, choose_video, title_match
from sportlock.exercises.infrastructure.youtube_videos import parse_search


def _video(title: str, seconds: int | None = 60, video_id: str = "x") -> VideoCandidate:
    return VideoCandidate(
        video_id=video_id, title=title, seconds=seconds, url=f"https://www.youtube.com/watch?v={video_id}"
    )


def test_choose_video__a_tutorial_naming_the_exercise__chosen_over_a_long_workout(catalogue):
    # given
    knee_plank = catalogue.get("knee-plank")
    candidates = [
        _video("20 Minute Core Workout with Knee Planks", seconds=1200, video_id="a"),
        _video("How To Properly Do A Knee Plank", seconds=95, video_id="b"),
    ]
    # when
    chosen = choose_video(knee_plank, candidates)
    # then
    assert chosen is not None and chosen.video_id == "b"


def test_choose_video__no_title_names_the_exercise__none(catalogue):
    chosen = choose_video(catalogue.get("knee-plank"), [_video("Best squat tips"), _video("Push-up challenge")])
    assert chosen is None


def test_choose_video__unknown_length__skipped(catalogue):
    assert choose_video(catalogue.get("knee-plank"), [_video("Knee plank", seconds=None)]) is None


def test_title_match__plurals_and_hyphens__still_match():
    assert title_match(["Incline push-up"], "How to do INCLINE PUSH UPS") == 1.0


def test_parse_search__yt_dlp_lines__candidates_without_the_broken_ones():
    output = (
        '{"id": "ic40NXcsQ2M", "title": "Knee Plank", "channel": "Wellen", "duration": 95.0,'
        ' "url": "https://www.youtube.com/watch?v=ic40NXcsQ2M"}\n'
        "not json\n"
        '{"id": "short1", "title": "Plank", "uploader": "Gym", "duration": null}\n'
    )
    assert [(c.video_id, c.channel, c.seconds) for c in parse_search(output)] == [
        ("ic40NXcsQ2M", "Wellen", 95),
        ("short1", "Gym", None),
    ]
