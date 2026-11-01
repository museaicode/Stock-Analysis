# -*- coding: utf-8 -*-
"""Neutral HTTP client identity for outbound requests.

Kept free of repository URLs or personal identifiers so third-party services
(notification servers, data providers) only see a generic product token.
"""

NEUTRAL_USER_AGENT = "MIUSIAI/1.0"
# Browser-compatible variant for endpoints that reject non-browser agents.
NEUTRAL_COMPATIBLE_USER_AGENT = f"Mozilla/5.0 (compatible; {NEUTRAL_USER_AGENT})"
