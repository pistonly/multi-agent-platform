"""T5-B experiment-cost-ledger subpackage.

Per-experiment token cost ledger: Layer 1 collector (raw event scan) +
Layer 2 mapper (version-keyed field normalization) + attribution (session →
experiment) + render (CLI tables). Single source of truth lives in
``lib/cost_ledger/``; this package holds the CLI surface.
"""
