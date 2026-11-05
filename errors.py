class ScraperError(RuntimeError):
    """Expected source or transport error safe to show in a report."""


class SourceConfigurationError(ScraperError):
    """Missing credentials or invalid source configuration."""


class SourceBlockedError(ScraperError):
    """A source denied automated access; no bypass is attempted."""


class RobotsDeniedError(SourceBlockedError):
    """robots.txt disallows the requested path."""
