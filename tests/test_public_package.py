# pylint: disable=import-error

import ballad
from ballad.application import (
    ReviewPlan,
    analyze_folder,
    apply_review_plan,
    undo_batch,
)


def test_public_package_exposes_import_safe_application_services():
    assert callable(ballad.main)
    assert ReviewPlan.__name__ == "ReviewPlan"
    assert callable(analyze_folder)
    assert callable(apply_review_plan)
    assert callable(undo_batch)
