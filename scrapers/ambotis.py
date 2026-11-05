from scrapers._samo import SamoScraper


class AmbotisScraper(SamoScraper):
    """Ambotis Holidays package tours through SAMO API."""

    name = "ambotis"
    base_url = "https://online.ambotis.ru"
    token_env = "AMBOTIS_API_TOKEN"
