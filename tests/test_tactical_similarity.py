import pytest
from tactical_match_engine.engine.tactical_similarity import cosine_similarity


def test_identical_vectors():
    assert cosine_similarity([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)

def test_orthogonal_vectors():
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)

def test_different_lengths():
    with pytest.raises(ValueError):
        cosine_similarity([1, 2], [1, 2, 3])

def test_zero_vector():
    assert cosine_similarity([0, 0, 0], [1, 2, 3]) == pytest.approx(0.0)
