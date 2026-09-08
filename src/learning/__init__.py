"""Phase 3b - the learning layer.

The corpus is survivorship-free and the endings are known. This package
turns that into something testable: a table of resolution episodes (which
fund ended how, and when), the announcement window before each ending,
a fixed vocabulary of pre-specified features read from those windows,
and the join onto the monthly research panel where the existing honest
tests (deciles, Fama-MacBeth, the 2022+ holdout) can say whether any of it
is knowable in advance.

Never an end-to-end text-to-return model: a few hundred independent
endings cannot support one, and the features are the unit of audit.
"""
