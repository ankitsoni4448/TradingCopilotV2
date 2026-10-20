"""Versioned candidate reference baskets for Crypto IDX synchronization research.

These baskets are hypotheses, not replicas of the target instrument. A basket's
identity is immutable and its return is computed from synchronized price pairs.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class BasketMember:
    symbol: str
    weight: float


@dataclass(frozen=True)
class ReferenceBasketSpec:
    name: str
    version: str
    members: tuple[BasketMember, ...]

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.version.strip():
            raise ValueError("basket name and version must be non-empty")
        if not self.members:
            raise ValueError("basket must have at least one member")
        symbols = [member.symbol for member in self.members]
        if any(not symbol.strip() for symbol in symbols):
            raise ValueError("basket symbols must be non-empty")
        if len(set(symbols)) != len(symbols):
            raise ValueError("basket symbols must be unique")
        if any(not math.isfinite(member.weight) or member.weight <= 0
               for member in self.members):
            raise ValueError("basket weights must be positive and finite")
        if not math.isclose(sum(member.weight for member in self.members), 1.0,
                            rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("basket weights must sum to 1")

    @property
    def identity(self) -> str:
        return f"{self.name}:{self.version}"

    @property
    def fingerprint(self) -> str:
        payload = {
            "name": self.name,
            "version": self.version,
            "members": sorted(
                ({"symbol": member.symbol, "weight": member.weight}
                 for member in self.members),
                key=lambda member: member["symbol"]),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def basket_log_return(spec: ReferenceBasketSpec,
                      entry_prices: Mapping[str, float],
                      exit_prices: Mapping[str, float]) -> float:
    """Return the weighted log return; reject incomplete or invalid evidence."""
    total = 0.0
    for member in spec.members:
        if member.symbol not in entry_prices or member.symbol not in exit_prices:
            raise ValueError(f"missing price for {member.symbol}")
        entry = float(entry_prices[member.symbol])
        exit_ = float(exit_prices[member.symbol])
        if not math.isfinite(entry) or not math.isfinite(exit_) or entry <= 0 or exit_ <= 0:
            raise ValueError(f"prices for {member.symbol} must be positive and finite")
        total += member.weight * math.log(exit_ / entry)
    return total


def return_direction(log_return: float, *, flat_tolerance: float = 0.0) -> str:
    if not math.isfinite(log_return) or flat_tolerance < 0:
        raise ValueError("return and flat_tolerance must be valid")
    if log_return > flat_tolerance:
        return "UP"
    if log_return < -flat_tolerance:
        return "DOWN"
    return "FLAT"


# Deliberately small null/candidate set. The proprietary top-10 composition and
# weights are not assumed; future versions require time-stamped evidence.
CANDIDATE_BASKETS = {
    "BTC_ONLY_V1": ReferenceBasketSpec(
        "BTC_ONLY", "v1", (BasketMember("BTCUSDT", 1.0),)),
    "ETH_ONLY_V1": ReferenceBasketSpec(
        "ETH_ONLY", "v1", (BasketMember("ETHUSDT", 1.0),)),
    "BTC_ETH_EQUAL_V1": ReferenceBasketSpec(
        "BTC_ETH_EQUAL", "v1",
        (BasketMember("BTCUSDT", 0.5), BasketMember("ETHUSDT", 0.5))),
}

# Collection expansion does not silently expand the preregistered V1 search.
# These hypotheses remain exploratory until a later registry version is frozen.
EXPLORATORY_BASKETS = {
    "MAJOR5_EQUAL_EXPLORATORY_V1": ReferenceBasketSpec(
        "MAJOR5_EQUAL_EXPLORATORY", "v1", (
            BasketMember("BTCUSDT", 0.2),
            BasketMember("ETHUSDT", 0.2),
            BasketMember("BNBUSDT", 0.2),
            BasketMember("SOLUSDT", 0.2),
            BasketMember("XRPUSDT", 0.2),
        )),
}
