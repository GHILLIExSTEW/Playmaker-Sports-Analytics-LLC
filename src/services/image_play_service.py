from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from src.config import (
    OPENAI_API_KEY,
    OPENAI_VISION_MODEL,
    OPENAI_VISION_MODELS,
)
from src.services.play_service import PlayService


class ImagePlayService:
    PROMPT = (
        "Read this betting slip. Return JSON only with keys: units (number), "
        "team_name (string or null), and legs (array). Each legs item must have "
        "selection (string) and odds (American integer or decimal format like 1.44). Do not guess unreadable text; "
        "explain uncertainty in the selection. When readable, each leg may also include: "
        "sport (nfl, ncaa for American college football, basketball, baseball, hockey, football for soccer, mma, or other), "
        "home_name and away_name (full team names), event_date (YYYY-MM-DD in America/New_York or null), "
        "market (moneyline, spread, total, or other for props/futures), side (home, away, over, under, or null), "
        "line (number or null), and scope (full_game, or partial for halves/quarters/periods/innings). "
        "Use null for any of these you cannot read; never guess them."
    )

    def extract_play(self, image_url: str, message_text: str = "") -> dict[str, Any]:
        errors = []
        providers = []
        if OPENAI_API_KEY:
            providers.append(("openai", "https://api.openai.com/v1/chat/completions", OPENAI_API_KEY, OPENAI_VISION_MODEL))
        for model in OPENAI_VISION_MODELS:
            if not any(entry[3] == model for entry in providers):
                providers.append((f"openai:{model}", "https://api.openai.com/v1/chat/completions", OPENAI_API_KEY, model))
        if not providers:
            raise RuntimeError("No vision provider is configured.")

        for name, url, api_key, model in providers:
            for attempt in range(2):
                try:
                    parsed = self._extract_with_provider(image_url, message_text, name, url, api_key, model)
                    text_units = self.extract_units_from_text(message_text)
                    if text_units is not None:
                        parsed["units"] = text_units
                    return parsed
                except Exception as exc:
                    errors.append(f"{name} attempt {attempt + 1}: {exc}")
                    if attempt == 0:
                        time.sleep(1)

        raise RuntimeError("All vision providers failed or blocked the image. " + " | ".join(errors))

    @staticmethod
    def extract_units_from_text(message_text: str) -> float | None:
        text = message_text or ""
        match = re.search(r"\bunits?\s*[:=]?\s*(\d+(?:\.\d+)?)\b", text, re.IGNORECASE)
        if match is None:
            match = re.search(r"(?<![\w$])(\d+(?:\.\d+)?)\s*u(?:nits?)?\b", text, re.IGNORECASE)
        return float(match.group(1)) if match else None

    def _extract_with_provider(self, image_url: str, message_text: str, name: str, url: str, api_key: str, model: str) -> dict[str, Any]:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": f"{self.PROMPT}\nDiscord message text, which may contain units: {message_text or '<none>'}"},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]}],
            },
            timeout=60,
        )
        if response.status_code in {400, 403, 408, 429} or response.status_code >= 500:
            detail = response.text[:300].replace("\n", " ")
            raise RuntimeError(f"provider response {response.status_code}: {detail}")
        response.raise_for_status()
        body = response.json()
        choice = (body.get("choices") or [{}])[0]
        finish_reason = choice.get("finish_reason")
        if finish_reason in {"blocked", "content_filter", "safety"}:
            raise RuntimeError(f"provider blocked image ({finish_reason})")
        content = (choice.get("message") or {}).get("content")
        if not content:
            raise RuntimeError("provider returned no content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        content = str(content).strip()
        if content.startswith("```"):
            content = content.removeprefix("```").removeprefix("json").removesuffix("```").strip()
        parsed = json.loads(content)
        self._validate(parsed)
        return parsed

    @staticmethod
    def _validate(data: dict[str, Any]) -> None:
        if data.get("units") in {None, "", "unknown", "Unknown"}:
            data["units"] = None
        else:
            try:
                units = float(data.get("units"))
            except (TypeError, ValueError):
                units = 0
            if units <= 0:
                raise ValueError("The image reader returned invalid units.")
            data["units"] = units
        legs = data.get("legs")
        if not isinstance(legs, list) or not 1 <= len(legs) <= 10:
            raise ValueError("The image reader could not find 1-10 legs.")
        for leg in legs:
            if not isinstance(leg.get("selection"), str) or not leg["selection"].strip():
                raise ValueError("At least one leg selection could not be read.")
            try:
                odds = PlayService.normalize_odds(leg.get("odds"))
            except (TypeError, ValueError):
                odds = 0
            if odds == 0:
                raise ValueError("At least one leg's odds could not be read.")
            leg["odds"] = odds


image_play_service = ImagePlayService()