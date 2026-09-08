"""Deterministic parsers. Importing this package registers every parser."""

from . import (  # noqa: F401  (import for side-effect: registration)
    admin_log,
    asx_3b,
    asx_3y,
    asx_quarterly,
    asx_substantial,
    rns_buyback,
    rns_pdmr,
    rns_tr1,
    us_8k,
    us_13dg,
    us_form4,
)
from .base import get_parser, parser_names

__all__ = ["get_parser", "parser_names"]
