"""
Location Resolver — geo-semantic pipeline that converts vague landmark
references into ranked, confirmable street addresses.

Uses OSM Nominatim + Data.gov.sg + fuzzy string matching.
"""

import logging
from typing import List, Optional
import httpx
from rapidfuzz import fuzz
from models import LocationAnchor, LocationCandidate
from config import settings

logger = logging.getLogger(__name__)

# Timeout for external API calls (seconds)
API_TIMEOUT = 8.0


class LocationResolver:
    """Resolves vague location anchors to ranked address candidates."""

    def __init__(self):
        self.nominatim_url = settings.NOMINATIM_BASE_URL
        self.datagovsg_url = settings.DATAGOVSG_API_URL

    async def resolve(
        self,
        anchors: List[LocationAnchor],
        country_code: str = "sg",
    ) -> List[LocationCandidate]:
        """
        Main entry point — resolve a list of location anchors to up to
        3 ranked candidates using OSM + Data.gov.sg sources.
        """
        all_candidates: List[LocationCandidate] = []

        for anchor in anchors:
            # Step 1: Query OSM Nominatim
            osm_results = await self._query_nominatim(anchor, country_code)
            all_candidates.extend(osm_results)

            # Step 2: Query Data.gov.sg POI
            govsg_results = await self._query_datagovsg(anchor)
            all_candidates.extend(govsg_results)

        # Step 3: Score and rank
        scored = self._score_candidates(all_candidates, anchors)

        # Step 4: Deduplicate and return top 3
        return self._deduplicate(scored)[:3]

    # ── OSM Nominatim ──────────────────────────────────────

    async def _query_nominatim(
        self, anchor: LocationAnchor, country_code: str
    ) -> List[LocationCandidate]:
        """Search OSM Nominatim with multilingual support."""
        params = {
            "q": anchor.text,
            "countrycodes": country_code,
            "format": "json",
            "limit": 5,
            "accept-language": "en,zh,ms,ta",
            "addressdetails": 1,
        }
        headers = {"User-Agent": "CrossLingualSafetyRadio/1.0"}

        try:
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.nominatim_url}/search", params=params, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()

            candidates = []
            for item in data:
                candidates.append(
                    LocationCandidate(
                        name=item.get("display_name", "").split(",")[0],
                        address=item.get("display_name", ""),
                        latitude=float(item.get("lat", 0)),
                        longitude=float(item.get("lon", 0)),
                        score=float(item.get("importance", 0)) * 40,  # Normalize to ~0-40
                        source="osm",
                    )
                )
            logger.info(f"Nominatim returned {len(candidates)} results for '{anchor.text}'")
            return candidates

        except Exception as e:
            logger.warning(f"Nominatim query failed for '{anchor.text}': {e}")
            return []

    # ── Data.gov.sg ────────────────────────────────────────

    async def _query_datagovsg(self, anchor: LocationAnchor) -> List[LocationCandidate]:
        """Search Data.gov.sg datasets for matching POIs."""
        # Search across multiple relevant datasets
        dataset_ids = [
            "d_1fa884d22922985df14f11bfdcde76ae",  # Places of worship
        ]
        candidates = []

        for dataset_id in dataset_ids:
            try:
                params = {
                    "resource_id": dataset_id,
                    "q": anchor.text,
                    "limit": 5,
                }
                async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                    resp = await client.get(self.datagovsg_url, params=params)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()

                records = data.get("result", {}).get("records", [])
                for rec in records:
                    name = rec.get("name", rec.get("BUILDING", rec.get("description", "")))
                    address = rec.get("address", rec.get("ADDRESS", rec.get("street", "")))
                    lat = float(rec.get("latitude", rec.get("LATITUDE", 0)) or 0)
                    lon = float(rec.get("longitude", rec.get("LONGITUDE", 0)) or 0)

                    if name:
                        candidates.append(
                            LocationCandidate(
                                name=name,
                                address=address or name,
                                latitude=lat,
                                longitude=lon,
                                score=0,  # Will be scored later
                                source="datagovsg",
                            )
                        )
                logger.info(f"Data.gov.sg ({dataset_id}) returned {len(records)} records for '{anchor.text}'")

            except Exception as e:
                logger.warning(f"Data.gov.sg query failed for '{anchor.text}': {e}")

        return candidates

    # ── Scoring ────────────────────────────────────────────

    def _score_candidates(
        self,
        candidates: List[LocationCandidate],
        anchors: List[LocationAnchor],
    ) -> List[LocationCandidate]:
        """Score each candidate using fuzzy name matching + heuristics."""
        anchor_texts = [a.text for a in anchors]

        for cand in candidates:
            # Fuzzy name match against all anchors (0-40 pts)
            name_scores = [
                fuzz.token_sort_ratio(a_text, cand.name) / 100 * 40
                for a_text in anchor_texts
            ]
            best_name_score = max(name_scores) if name_scores else 0

            # Address match (0-20 pts)
            addr_scores = [
                fuzz.partial_ratio(a_text, cand.address) / 100 * 20
                for a_text in anchor_texts
            ]
            best_addr_score = max(addr_scores) if addr_scores else 0

            # Source bonus: Data.gov.sg data is official (0-15 pts)
            source_bonus = 15 if cand.source == "datagovsg" else 5

            # Proximity bonus placeholder (0-25 pts) — would use actual coords in production
            proximity_bonus = 10  # Default moderate bonus

            cand.score = best_name_score + best_addr_score + source_bonus + proximity_bonus

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    # ── Deduplication ──────────────────────────────────────

    def _deduplicate(self, candidates: List[LocationCandidate]) -> List[LocationCandidate]:
        """Remove near-duplicate candidates using fuzzy matching on address."""
        unique: List[LocationCandidate] = []
        for cand in candidates:
            is_dup = False
            for existing in unique:
                if fuzz.ratio(cand.address, existing.address) > 80:
                    is_dup = True
                    break
            if not is_dup:
                unique.append(cand)
        return unique

    def generate_confirmation_question(
        self, candidates: List[LocationCandidate]
    ) -> str:
        """
        Generate a yes/no confirmation question for the dispatcher
        when the top candidate scores below the confidence threshold.
        """
        if not candidates:
            return "Could you ask the caller for more location details?"

        top = candidates[0]
        confidence_pct = min(int(top.score), 100)

        if confidence_pct >= 75:
            return f"Location identified: {top.name} — {top.address} ({confidence_pct}% confidence)"
        else:
            return (
                f"Are you near {top.name}? Just say yes or no. "
                f"(Best match: {top.address}, {confidence_pct}% confidence)"
            )


# Singleton
location_resolver = LocationResolver()
