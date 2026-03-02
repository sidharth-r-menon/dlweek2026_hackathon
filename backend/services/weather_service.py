"""
Weather Service — Open-Meteo (NOAA-sourced) weather context for
incident enrichment and location candidate scoring.
"""

import logging
from typing import Optional
import httpx
from config import settings

logger = logging.getLogger(__name__)


class WeatherService:
    """Fetches weather context for a given location."""

    def __init__(self):
        self.base_url = settings.OPENMETEO_API_URL

    async def get_weather_context(
        self,
        latitude: float,
        longitude: float,
    ) -> str:
        """
        Get a human-readable weather summary for a location.
        Used to enrich the incident narrative.

        Returns a short string like:
        "Heavy rain (12mm/hr), 28°C, high humidity. Flooding risk."
        """
        if latitude == 0 and longitude == 0:
            return "Weather data unavailable (no coordinates)"

        try:
            params = {
                "latitude": latitude,
                "longitude": longitude,
                "current_weather": "true",
                "hourly": "precipitation,relative_humidity_2m",
                "forecast_days": 1,
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(self.base_url, params=params)
                resp.raise_for_status()
                data = resp.json()

            current = data.get("current_weather", {})
            temp = current.get("temperature", "N/A")
            windspeed = current.get("windspeed", 0)
            weather_code = current.get("weathercode", 0)

            # Map WMO weather codes to descriptions
            description = self._weather_code_to_text(weather_code)

            summary = f"{description}, {temp}°C, wind {windspeed} km/h"

            # Check precipitation from hourly
            hourly = data.get("hourly", {})
            precip = hourly.get("precipitation", [0])
            if precip and max(precip[:3]) > 5:
                summary += ". Heavy rain — flooding possible."
            elif precip and max(precip[:3]) > 1:
                summary += ". Light rain."

            logger.info(f"Weather context: {summary}")
            return summary

        except Exception as e:
            logger.warning(f"Weather fetch failed: {e}")
            return "Weather data unavailable"

    def _weather_code_to_text(self, code: int) -> str:
        """Convert WMO weather code to readable text."""
        mapping = {
            0: "Clear sky",
            1: "Mainly clear",
            2: "Partly cloudy",
            3: "Overcast",
            45: "Fog",
            48: "Rime fog",
            51: "Light drizzle",
            53: "Moderate drizzle",
            55: "Dense drizzle",
            61: "Slight rain",
            63: "Moderate rain",
            65: "Heavy rain",
            71: "Slight snow",
            73: "Moderate snow",
            75: "Heavy snow",
            80: "Slight rain showers",
            81: "Moderate rain showers",
            82: "Violent rain showers",
            95: "Thunderstorm",
            96: "Thunderstorm with slight hail",
            99: "Thunderstorm with heavy hail",
        }
        return mapping.get(code, f"Weather code {code}")


# Singleton
weather_service = WeatherService()
