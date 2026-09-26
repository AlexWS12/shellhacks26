# Sperry's 6 overlaps, to 0.01 mi and the exact day. Keep this green.

import pytest

from app.config import SAMPLE_XLSX
from app.core.overlap import distance_mi, is_overlap, time_gap_days
from app.core.sample import read_sample

EXPECTED = {"OVL_1": (4.09, 3074), "OVL_2": (5.65, 152), "OVL_3": (7.55, 517),
            "OVL_4": (8.01, 3074), "OVL_5": (14.34, 365), "OVL_6": (14.81, 730)}


@pytest.fixture(scope="module")
def sample():
    return read_sample(SAMPLE_XLSX)


def test_six_reference_overlaps(sample):
    assert {o.overlap_id for o in sample.overlaps} == set(EXPECTED)
    for o in sample.overlaps:
        a, b = sample.projects[o.a], sample.projects[o.b]
        d = distance_mi(a.center, b.center)
        assert round(d, 2) == pytest.approx(EXPECTED[o.overlap_id][0], abs=0.01), o.overlap_id
        assert round(d, 2) == pytest.approx(o.distance_mi, abs=0.01), o.overlap_id
        assert is_overlap(d)
        assert time_gap_days(a.in_service, b.in_service) == o.time_gap_days == EXPECTED[o.overlap_id][1]


def test_no_other_sample_pairs_overlap(sample):
    listed = {(o.a, o.b) for o in sample.overlaps}
    desc = [k for k in sample.projects if k.startswith("DESC")]
    gpc = [k for k in sample.projects if k.startswith("GPC")]
    for a in desc:
        for b in gpc:
            d = distance_mi(sample.projects[a].center, sample.projects[b].center)
            assert is_overlap(d) == ((a, b) in listed), (a, b, round(d, 2))
