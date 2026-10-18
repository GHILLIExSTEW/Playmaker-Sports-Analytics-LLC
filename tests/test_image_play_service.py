from types import SimpleNamespace

import pytest

from src.services import image_play_service as module
from src.services.image_play_service import ImagePlayService


def test_image_play_validation_accepts_structured_legs():
    ImagePlayService._validate({
        "units": 2,
        "team_name": "Colts",
        "legs": [{"selection": "Moneyline", "odds": -200}],
    })


def test_image_play_validation_rejects_missing_odds():
    with pytest.raises(ValueError):
        ImagePlayService._validate({
            "units": 2,
            "legs": [{"selection": "Moneyline", "odds": 0}],
        })


def test_image_play_validation_normalizes_string_numbers():
    data = {"units": "2", "legs": [{"selection": "Moneyline", "odds": "-110"}]}
    ImagePlayService._validate(data)
    assert data["units"] == 2.0
    assert data["legs"][0]["odds"] == -110


def test_extract_units_from_message_text():
    assert ImagePlayService.extract_units_from_text("2U") == 2.0
    assert ImagePlayService.extract_units_from_text("Units: 2.5") == 2.5
    assert ImagePlayService.extract_units_from_text("$20 stake") is None


def test_image_play_validation_normalizes_decimal_odds():
    data = {"units": 2, "legs": [{"selection": "Moneyline", "odds": "1.44"}]}
    ImagePlayService._validate(data)
    assert data["legs"][0]["odds"] == 44


def response(content, status=200):
    return SimpleNamespace(
        status_code=status, text=str(content), raise_for_status=lambda: None,
        json=lambda: {"choices": [{"finish_reason": "stop", "message": {"content": content}}]},
    )


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(module, "OPENAI_API_KEY", "key")
    monkeypatch.setattr(module, "OPENAI_VISION_MODEL", "model-a")
    monkeypatch.setattr(module, "OPENAI_VISION_MODELS", ["model-a", "model-b"])
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    return ImagePlayService()


def test_text_ballot_without_odds_reports_the_model_reason_not_a_provider_outage(service, monkeypatch):
    prompts = []

    def post(url, **kwargs):
        prompts.append(kwargs["json"]["messages"][0]["content"][0]["text"])
        return response('{"readable": false, "reason": "No odds are given for either moneyline.", "legs": []}')
    monkeypatch.setattr(module.requests, "post", post)
    with pytest.raises(module.BallotUnreadable) as error:
        service.extract_text_play("Panthers ml + Chiefs ml")
    message = str(error.value)
    assert message.startswith("The ballot bet text could not be read unambiguously. No odds are given")
    assert "4 of 4 reads agreed" in message
    assert "vision providers" not in message
    assert "reason" in prompts[0]


def test_unreadable_image_without_reason_names_the_image(service, monkeypatch):
    monkeypatch.setattr(module.requests, "post", lambda url, **kwargs: response('{"readable": false}'))
    with pytest.raises(module.BallotUnreadable, match=r"^The ballot bet image could not be read unambiguously\. \("):
        service.extract_play("https://cdn/slip.png", "", require_readable=True)


def test_provider_outages_still_report_every_attempt(service, monkeypatch):
    replies = iter([response('{"readable": false}')] + [response("busy", 503)] * 3)
    monkeypatch.setattr(module.requests, "post", lambda url, **kwargs: next(replies))
    with pytest.raises(RuntimeError, match="All vision providers failed") as error:
        service.extract_text_play("Panthers ml -150")
    assert not isinstance(error.value, module.BallotUnreadable)
    assert "provider response 503" in str(error.value)


def test_readable_text_ballot_still_parses(service, monkeypatch):
    monkeypatch.setattr(module.requests, "post", lambda url, **kwargs: response(
        '{"readable": true, "units": null, "legs": [{"selection": "Panthers ML", "odds": -150}]}'))
    assert service.extract_text_play("Panthers ml -150")["legs"] == [{"selection": "Panthers ML", "odds": -150}]