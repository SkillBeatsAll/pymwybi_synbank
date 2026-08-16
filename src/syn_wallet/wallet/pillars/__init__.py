"""One module per product pillar.

Each exposes ``build(features) -> common.PillarOutput`` and owns the economic
argument for its own denominator. They share the confidence engine, the share
guards and the assumption registry, and nothing else.
"""

from . import cash, fx, investment_banking, lending, trade

__all__ = ["cash", "fx", "investment_banking", "lending", "trade"]
