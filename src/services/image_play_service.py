from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from src.config import (
    OPENAI_API_KEY,
    OPENAI_VISION_DAILY_LIMIT,
    OPENAI_VISION_MODEL,
    OPENAI_VISION_MODELS,
)
from src.services.play_service import PlayService
from src.services.supabase_service import supabase_service


class BallotUnreadable(ValueError):
    """The model answered, but the bet did not state every selection and its odds clearly."""


class VisionBudgetDenied(RuntimeError):
    """The shared daily OpenAI slip-reading allowance is used up; no call was made."""


class TransientProviderError(RuntimeError):
    """Timeouts, rate limits and 5xx responses: worth one more try on the same model."""


def reserve_vision_call() -> None:
    """Atomically count one paid vision call against the shared UTC-day ceiling (fail closed)."""
    if not OPENAI_VISION_DAILY_LIMIT:
        return
    reserved = supabase_service._execute(lambda: supabase_service._ensure_client().rpc(
        "reserve_openai_vision_request", {"p_daily_limit": OPENAI_VISION_DAILY_LIMIT},
    ).execute()).data
    if reserved is not True:
        raise VisionBudgetDenied(
            "The daily slip-reading allowance is used up. Enter this play manually with /play or try tomorrow."
        )


class ImagePlayService:
    PROMPT = (
        "Read this betting slip. Return JSON only with keys: units (number), "
        "team_name (string or null), and legs (array). Each legs item must have "
        "selection (string) and odds (American integer, or a payout multiplier written with its x such as \"1.99x\" "
        "as Sleeper shows; never convert multipliers yourself). Do not guess unreadable text; "
        "explain uncertainty in the selection. When readable, each leg may also include: "
        "sport (nfl, ncaa for American college football, basketball, baseball, hockey, football for soccer, mma, or other), "
        "home_name and away_name (full team names), event_date (YYYY-MM-DD in America/New_York or null), "
        "event_start (ISO 8601 date and time with a timezone offset, only if explicitly readable; otherwise null), "
        "market (moneyline, spread, total, or other for props/futures), side (home, away, over, under, or null), "
        "line (number or null), and scope (full_game, or partial for halves/quarters/periods/innings). "
        "Use null for any of these you cannot read; never guess them."
    )

    def extract_play(
        self, image_url: str, message_text: str = "", *, require_readable: bool = False, allow_missing_odds: bool = False,
    ) -> dict[str, Any]:
        errors = []
        unreadable: list[BallotUnreadable] = []
        if not OPENAI_API_KEY:
            # Previously this sent "Bearer None" requests to every configured model, twice each.
            raise RuntimeError("No vision provider is configured (OPENAI_API_KEY is not set).")
        url = "https://api.openai.com/v1/chat/completions"
        providers = [("openai", url, OPENAI_API_KEY, OPENAI_VISION_MODEL)]
        for model in OPENAI_VISION_MODELS:
            if not any(entry[3] == model for entry in providers):
                providers.append((f"openai:{model}", url, OPENAI_API_KEY, model))

        # Each model gets one read. Only transient failures (timeouts, 429, 5xx) earn a second
        # try on the same model; an unreadable or invalid read moves straight to the next model,
        # because asking the same model the same question again usually just doubles the cost.
        for name, url, api_key, model in providers:
            for attempt in range(2):
                try:
                    reserve_vision_call()
                    parsed = self._extract_with_provider(
                        image_url, message_text, name, url, api_key, model,
                        require_readable=require_readable, allow_missing_odds=allow_missing_odds,
                    )
                    text_units = self.extract_units_from_text(message_text)
                    if text_units is not None:
                        parsed["units"] = text_units
                    return parsed
                except VisionBudgetDenied:
                    raise
                except Exception as exc:
                    errors.append(f"{name} attempt {attempt + 1}: {exc}")
                    if isinstance(exc, BallotUnreadable):
                        unreadable.append(exc)
                    transient = isinstance(exc, TransientProviderError) or (
                        isinstance(exc, requests.RequestException) and not isinstance(exc, requests.HTTPError)
                    )
                    if attempt == 0 and transient:
                        time.sleep(1)
                        continue
                    break

        if unreadable and len(unreadable) == len(errors):
            # Every model read the bet and declined it; this is a content problem, not a provider outage.
            raise BallotUnreadable(f"{unreadable[-1]} ({len(unreadable)} of {len(errors)} reads agreed.)")
        raise RuntimeError("All vision providers failed or blocked the image. " + " | ".join(errors))

    def extract_text_play(self, message_text: str) -> dict[str, Any]:
        if not message_text.strip():
            raise ValueError("The ballot has no readable bet text.")
        return self.extract_play("", message_text, require_readable=True)

    @staticmethod
    def extract_units_from_text(message_text: str) -> float | None:
        text = message_text or ""
        match = re.search(r"\bunits?\s*[:=]?\s*(\d+(?:\.\d+)?)\b", text, re.IGNORECASE)
        if match is None:
            match = re.search(r"(?<![\w$])(\d+(?:\.\d+)?)\s*u(?:nits?)?\b", text, re.IGNORECASE)
        return float(match.group(1)) if match else None

    def _extract_with_provider(
        self, image_url: str, message_text: str, name: str, url: str, api_key: str, model: str,
        *, require_readable: bool = False, allow_missing_odds: bool = False,
    ) -> dict[str, Any]:
        prompt = self.PROMPT
        if not image_url:
            prompt = prompt.replace("Read this betting slip.", "Read the bet from the supplied Discord text.")
        if allow_missing_odds:
            prompt += (
                " If a leg's odds are not shown or not clearly legible, set that leg's odds to null; "
                "the author will fill them in. Never invent odds."
            )
        if require_readable:
            prompt += (
                " Also return readable (boolean): true only when every selection"
                f"{'' if allow_missing_odds else ' and its odds'} is fully readable "
                "and unambiguous. Otherwise return readable:false; do not guess or substitute uncertainty text. "
                "When readable is false, also return reason: one short sentence naming what is missing or unclear "
                "(for example, no odds are given for a selection)."
            )
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "store": False,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": f"{prompt}\nDiscord message text, which may contain units: {message_text or '<none>'}"},
                ] + ([{"type": "image_url", "image_url": {"url": image_url}}] if image_url else [])}],
            },
            timeout=60,
        )
        if response.status_code in {408, 429} or response.status_code >= 500:
            detail = response.text[:300].replace("\n", " ")
            raise TransientProviderError(f"provider response {response.status_code}: {detail}")
        if response.status_code in {400, 403}:
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
        if require_readable and parsed.get("readable") is not True:
            source = "image" if image_url else "text"
            reason = parsed.get("reason")
            detail = f" {str(reason).strip()[:200]}" if isinstance(reason, str) and reason.strip() else ""
            raise BallotUnreadable(f"The ballot bet {source} could not be read unambiguously.{detail}")
        self._validate(parsed, allow_missing_odds=allow_missing_odds)
        return parsed

    @staticmethod
    def _validate(data: dict[str, Any], *, allow_missing_odds: bool = False) -> None:
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
                if not allow_missing_odds:
                    raise ValueError("At least one leg's odds could not be read.")
                odds = None
            leg["odds"] = odds


image_play_service = ImagePlayService()