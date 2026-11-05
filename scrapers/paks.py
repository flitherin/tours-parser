from scrapers._samo import SamoScraper


class PaksScraper(SamoScraper):
    """PAKS package tours from the licensed SAMO gateway."""

    name = "paks"
    base_url = "https://newonline.paks.ru"
    token_env = "PAKS_API_TOKEN"
