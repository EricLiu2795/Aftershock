"""Operational pipeline errors. These are application failures, not AI output."""


class OperationalError(ValueError):
    """Base error for deterministic operational-state processing."""


class InvalidObservationError(OperationalError):
    """StructuredObservation failed contract validation or was unusable."""


class UnknownSiteError(OperationalError):
    """A referenced canonical site_id is not present in the SiteRegistry."""
