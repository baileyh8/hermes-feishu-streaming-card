"""Stable installer error identities, independent of execution owners."""


class IntegrityRepairRefused(ValueError):
    pass


class RecoveryRefused(ValueError):
    pass
