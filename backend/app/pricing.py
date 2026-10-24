"""What a model call costs, from the tokens it used. Used by the eval report.

The prices are the ones in Langfuse's model table on 2026-10-23, not the provider's own page,
so confirm them before quoting a dollar figure. Langfuse also has tiers for this model (for
example for cached input) that this table leaves out: every input token is priced as a fresh
one, so the cost here is an upper bound. Output tokens include the model's hidden reasoning.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    """Dollars per million tokens."""

    input_per_million: float
    output_per_million: float

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens * self.input_per_million + completion_tokens * self.output_per_million
        ) / 1_000_000


PRICES: dict[str, ModelPrice] = {
    "gpt-6-luna": ModelPrice(input_per_million=0.10, output_per_million=0.50),
}


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """The cost in dollars, or None if the model has no price in the table."""
    price = PRICES.get(model)
    return price.cost(prompt_tokens, completion_tokens) if price else None
