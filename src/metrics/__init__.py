"""Derived metrics. Every function documents its formula, inputs, window,
units and the definition it follows (Glassnode's own metric descriptions,
read from studio.glassnode.com on 2026-10-21, unless another source is
cited). Anything approximated is flagged with "APPROX:" in its docstring.

All functions take a `Ctx` (cached access to stored raw series) and return a
pandas Series or DataFrame indexed by UTC timestamps (daily at 00:00 UTC unless
stated). Nothing here calls an external API.
"""
from .core import Ctx  # noqa: F401
