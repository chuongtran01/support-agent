from app.tools.checks import check_quantity


def test_whole_line_can_be_returned():
    assert check_quantity(requested=2, bought=2, refunded=0) is None


def test_part_of_a_line_can_be_returned():
    assert check_quantity(requested=1, bought=3, refunded=0) is None


def test_units_already_refunded_are_not_available_again():
    assert check_quantity(requested=1, bought=2, refunded=1) is None
    assert check_quantity(requested=2, bought=2, refunded=1) == "Only 1 unit can still be refunded."


def test_one_unit_too_many():
    assert (
        check_quantity(requested=3, bought=2, refunded=0) == "Only 2 units can still be refunded."
    )


def test_a_line_refunded_in_full_has_nothing_left():
    assert (
        check_quantity(requested=1, bought=1, refunded=1)
        == "This item has already been fully refunded."
    )


def test_quantity_must_be_positive():
    assert check_quantity(requested=0, bought=1, refunded=0) == "The quantity must be at least 1."
    assert check_quantity(requested=-1, bought=1, refunded=0) == "The quantity must be at least 1."
