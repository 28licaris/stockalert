"""Strategy library — subscription-facing catalog of strategies.

HARD RULE: the strategy `config` (the secret recipe) lives only in
StrategyDefinition (owner-only). Subscriber surfaces use StrategyPublic /
StrategyAlert, which have NO config field — the recipe cannot leak through them.
Every saved definition is backed up to S3 for safety.
"""
