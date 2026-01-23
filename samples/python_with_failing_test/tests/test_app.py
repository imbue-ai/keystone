from app import add, multiply


def test_add() -> None:
    assert add(1, 2) == 3


def test_multiply() -> None:
    assert multiply(2, 3) == 6


def test_impossible() -> None:
    """This test can never pass - it's designed to always fail."""
    assert False, "This test is intentionally designed to always fail"
