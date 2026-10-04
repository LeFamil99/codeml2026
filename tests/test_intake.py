import os
import sys

import pymupdf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from intake import project_name, store
from l2c.da.dashboard import configured_inputs, input_stamp_for, file_checkpoint


def pdf_bytes(pages=1):
    with pymupdf.open() as doc:
        for _ in range(pages):
            doc.new_page()
        return doc.tobytes()


def test_project_name_follows_the_plan_file_name():
    assert project_name("L2C_PLAN_STR_CLP.pdf") == "CLP"


def test_same_file_gets_the_same_path_and_is_not_rewritten(tmp_path):
    data = pdf_bytes()
    first = store("CLP_RADIERS.pdf", data, "CLP", root=str(tmp_path))
    mtime = os.path.getmtime(first)
    second = store("CLP_RADIERS.pdf", data, "CLP", root=str(tmp_path))
    assert first == second and os.path.getmtime(second) == mtime


def test_same_name_with_different_content_gets_a_different_path(tmp_path):
    a = store("CLP_RADIERS.pdf", pdf_bytes(1), "CLP", root=str(tmp_path))
    b = store("CLP_RADIERS.pdf", pdf_bytes(2), "CLP", root=str(tmp_path))
    assert a != b and os.path.basename(a) == os.path.basename(b)


def test_identical_names_and_content_in_another_session_reuse_the_same_keys(tmp_path):
    data = pdf_bytes()
    one = tmp_path / "session_one"
    two = tmp_path / "session_two"
    a = store("CLP_RADIERS.pdf", data, "CLP", root=str(one))
    b = store("CLP_RADIERS.pdf", data, "CLP", root=str(two))
    assert a != b
    stamp_a = input_stamp_for({"radier": [a]})
    stamp_b = input_stamp_for({"radier": [b]})
    assert stamp_a == stamp_b
    cache = tmp_path / "cache"
    assert file_checkpoint("radier", a, cache)[1] == file_checkpoint("radier", b, cache)[1]


def test_changed_content_invalidates_the_stamp_and_the_checkpoint(tmp_path):
    cache = tmp_path / "cache"
    a = store("CLP_RADIERS.pdf", pdf_bytes(1), "CLP", root=str(tmp_path / "x"))
    b = store("CLP_RADIERS.pdf", pdf_bytes(2), "CLP", root=str(tmp_path / "x"))
    assert input_stamp_for({"radier": [a]}) != input_stamp_for({"radier": [b]})
    assert file_checkpoint("radier", a, cache)[1] != file_checkpoint("radier", b, cache)[1]


def test_empty_categories_contribute_nothing_to_the_stamp(tmp_path):
    a = store("CLP_RADIERS.pdf", pdf_bytes(), "CLP", root=str(tmp_path))
    assert input_stamp_for({"radier": [a], "colonne": []}) == input_stamp_for({"radier": [a]})


def test_clp_is_the_default_parsing_method_and_reads_all_five_categories():
    from l2c.da import methods
    from l2c.da.dashboard import run_da
    assert methods.DEFAULT_METHOD == "clp"
    clp = methods.get("clp")
    assert clp.runner is run_da
    assert set(clp.categories) == {"colonne", "dalle", "semelle", "poutre", "radier"}


def test_unknown_parsing_method_is_refused():
    import pytest
    from l2c.da import methods
    with pytest.raises(ValueError, match="inconnue"):
        methods.get("does-not-exist")
