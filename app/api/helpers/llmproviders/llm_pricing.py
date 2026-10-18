"""LLM price list: USD per million tokens, per model. Add a model's price before using it.

Azure Global Standard list prices (2026-10). Data Zone deployments cost about 10% more;
gpt-5.5's long-context rate (over 272K input tokens) is not modelled.
"""

from decimal import Decimal
from typing import Final

from api.consts import OpenAIModel
from api.pydanticmodels.llm_models import LLMPricing

OPENAI_PRICING: Final[dict[OpenAIModel, LLMPricing]] = {
    OpenAIModel.GPT_5_5: LLMPricing(
        input_usd_per_million=Decimal("5.00"),
        cached_input_usd_per_million=Decimal("0.50"),
        output_usd_per_million=Decimal("30.00"),
    ),
    OpenAIModel.GPT_4O: LLMPricing(
        input_usd_per_million=Decimal("2.50"),
        cached_input_usd_per_million=Decimal("1.25"),
        output_usd_per_million=Decimal("10.00"),
    ),
}
