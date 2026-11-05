from scrapers._samo import SamoScraper


class SpaceTravelScraper(SamoScraper):
    """Space Travel package tours through SAMO API."""

    name = "space_travel"
    base_url = "https://online.space-travel.ru"
    token_env = "SPACE_TRAVEL_API_TOKEN"
