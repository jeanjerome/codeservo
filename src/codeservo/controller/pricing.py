"""Rating what a session consumed, at the catalogue's list prices.

An adapter reports tokens; it never prices them. The price is a policy over
that measurement, applied here with the one table both backends are rated by,
so a Codex run and a Claude run are comparable on the same arithmetic. Where
the table cannot rate what was reported, the cost stays unknown rather than
understated, and the record says at which model the tokens were rated and why.
"""

from __future__ import annotations

from ..actuators.base import Usage
from ..actuators.catalogue import Backend, Catalogue, rate
from .document import Consumption, PriceBasis, PricedUsage


def consumption(
    catalogue: Catalogue, backend: Backend, requested_model: str, usage: Usage
) -> Consumption:
    """What one call consumed, rated block by block, and the sum when every block is.

    A block the catalogue has no price for, or one the table cannot rate,
    leaves its own cost and the total unknown: a sum over part of a session
    would read as the cost of the whole.
    """
    items: list[PricedUsage] = []
    for billed in usage.billed:
        model = billed.model if billed.model is not None else requested_model
        basis = (
            PriceBasis.REPORTED_MODEL
            if billed.model is not None
            else PriceBasis.REQUESTED_MODEL
        )
        entry = catalogue.find(backend, model)
        cost = (
            rate(entry.price, billed.tokens.to_document(), usage.cache_write_duration)
            if entry is not None and entry.price is not None
            else None
        )
        items.append(
            PricedUsage(
                model=model,
                basis=basis,
                tokens=billed.tokens,
                cost_usd=cost,
                reported_cost_usd=billed.reported_cost_usd,
            )
        )
    costs = [item.cost_usd for item in items]
    total = (
        round(sum(cost for cost in costs if cost is not None), 6)
        if items and all(cost is not None for cost in costs)
        else None
    )
    return Consumption(items=tuple(items), cost_usd=total)
