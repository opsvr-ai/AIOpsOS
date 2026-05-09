"""Dedicated property-based test modules.

This package collects PBT suites that target specific correctness
properties from spec design documents. Each module name encodes the
property being validated (e.g. ``test_hotreload_6_sentinel_replaced``
⇒ P-HotReload-6). Tests here are kept separate from unit / integration
suites because they can be slow under Hypothesis' default example
budget and because they bind tightly to a single named property rather
than a module or class surface.
"""
