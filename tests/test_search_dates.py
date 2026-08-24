from ingest.dates import date_bound


def test_year_start():
    assert date_bound("2015", end=False) == "2015-01-01T00:00:00Z"


def test_year_end():
    assert date_bound("2015", end=True) == "2015-12-31T23:59:59Z"


def test_full_date():
    assert date_bound("2015-07-14", end=False) == "2015-07-14T00:00:00Z"
    assert date_bound("2015-07-14", end=True) == "2015-07-14T23:59:59Z"
