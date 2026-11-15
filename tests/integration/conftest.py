import pytest

from btkit.lab import Lab, Radio


@pytest.fixture(scope="session")
def radio():
    r = Radio(); yield r; r.stop()


@pytest.fixture
def lab():
    l = Lab(); l.reset(); yield l
