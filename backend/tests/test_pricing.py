from app.pricing import PRICES, ModelPrice, cost_usd


def test_cost_is_tokens_times_the_price_per_million():
    price = ModelPrice(input_per_million=2.0, output_per_million=10.0)
    assert price.cost(500_000, 100_000) == 2.0  # $1.00 in and $1.00 out


def test_an_unknown_model_has_no_cost_rather_than_zero():
    assert cost_usd("not-a-model", 1000, 1000) is None


def test_the_agent_model_is_in_the_table():
    from app.config import Settings

    assert Settings.model_fields["agent_model"].default in PRICES
