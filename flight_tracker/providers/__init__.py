"""Flight price sources. Each provider turns a (Trip, Search) into a list of Offers."""

from __future__ import annotations

import os

from .base import Offer, Provider
from .mock import MockProvider
from .serpapi import SerpApiProvider


def available_providers(use_mock: bool = False) -> list[Provider]:
    """Providers whose credentials are present in the environment."""
    if use_mock:
        return [MockProvider()]
    providers: list[Provider] = []
    if os.environ.get("SERPAPI_KEY"):
        providers.append(SerpApiProvider(os.environ["SERPAPI_KEY"]))
    return providers


__all__ = ["Offer", "Provider", "available_providers"]
