from services.embeddings.base import l2_normalize


def test_zero_vector_unchanged():
    assert l2_normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_already_unit_stays_unit():
    out = l2_normalize([0.6, 0.8])
    assert abs(out[0] - 0.6) < 1e-12
    assert abs(out[1] - 0.8) < 1e-12


def test_nonzero_becomes_unit():
    out = l2_normalize([3.0, 4.0])
    assert abs(out[0] - 0.6) < 1e-12
    assert abs(out[1] - 0.8) < 1e-12
