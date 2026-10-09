import json

from sportlock.exercises.infrastructure.details_repository import FileExerciseDetailsRepository
from sportlock.exercises.infrastructure.notebooklm_library import stick_figure


def test_from_seed__chains__link_easier_and_harder(catalogue):
    incline = catalogue.get("incline-push-up")
    assert (incline.easier, incline.harder) == ("wall-push-up", "knee-push-up")
    assert catalogue.get("wall-push-up").easier is None


def test_from_seed__every_exercise__has_a_known_kind_and_valid_sides(catalogue):
    for exercise in catalogue.all():
        assert exercise.kind in ("reps", "hold", "timed"), exercise.id
        assert exercise.sides in (None, "each", "alternating"), exercise.id


def test_easiest_available__missing_equipment__walks_down_the_chain(catalogue):
    assert catalogue.easiest_available("incline-push-up", set()) == "wall-push-up"
    assert catalogue.easiest_available("pull-up", set()) is None


def test_details_repository__built_entry__read_with_an_absolute_picture_path(tmp_path):
    (tmp_path / "plank").mkdir()
    (tmp_path / "plank" / "exercise.json").write_text(
        json.dumps(
            {"steps": ["a", "b"], "cues": ["x"], "image": "picture.jpg", "image_source": "book: B", "built_at": "now"}
        )
    )
    details = FileExerciseDetailsRepository(tmp_path).get("plank")
    assert (details.steps, details.cues, details.built) == (("a", "b"), ("x",), True)
    assert details.image == str(tmp_path / "plank" / "picture.jpg")


def test_details_repository__unbuilt_exercise__empty_details(tmp_path):
    details = FileExerciseDetailsRepository(tmp_path).get("plank")
    assert (details.steps, details.image, details.built) == ((), "", False)


def test_stick_figure__every_pattern__is_svg():
    for pattern in ("push", "pull", "squat", "hinge", "core", "mobility", "warmup", "unknown"):
        assert stick_figure(pattern).startswith("<svg")
