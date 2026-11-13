import pytest

from .cases import CASES

pytestmark = pytest.mark.live  # live Valio API: excluded from the default run


@pytest.mark.parametrize("category,title,fn", CASES, ids=[c[2].__name__ for c in CASES])
def test_trap(category, title, fn):
    ok, detail = fn()
    if ok is None:
        pytest.skip(detail)
    assert ok, f"{title}: {detail}"
