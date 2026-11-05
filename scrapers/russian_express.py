from scrapers._samo import SamoScraper


class RussianExpressScraper(SamoScraper):
    """Russian Express package tours through SAMO API."""

    name = "russian_express"
    base_url = "https://online.r-express.ru"
    token_env = "RUSSIAN_EXPRESS_API_TOKEN"
