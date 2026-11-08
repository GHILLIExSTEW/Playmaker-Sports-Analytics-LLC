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
    # One read per model: an unreadable answer is not retried on the same model.
    assert "2 of 2 reads agreed" in message
    assert len(prompts) == 2
    assert "vision providers" not in message
    assert "reason" in prompts[0]


def test_unreadable_image_without_reason_names_the_image(service, monkeypatch):
    monkeypatch.setattr(module.requests, "post", lambda url, **kwargs: response('{"readable": false}'))
    with pytest.raises(module.BallotUnreadable, match=r"^The ballot bet image could not be read unambiguously\. \("):
        service.extract_play("https://cdn/slip.png", "", require_readable=True)


def test_provider_outages_still_report_every_attempt(service, monkeypatch):
    replies = iter([response('{"readable": false}')] + [response("busy", 503)] * 2)
    calls = []
    monkeypatch.setattr(module.requests, "post", lambda url, **kwargs: calls.append(1) or next(replies))
    with pytest.raises(RuntimeError, match="All vision providers failed") as error:
        service.extract_text_play("Panthers ml -150")
    assert not isinstance(error.value, module.BallotUnreadable)
    assert "provider response 503" in str(error.value)
    # model-a: one unreadable read; model-b: a 503 and one transient retry.
    assert len(calls) == 3


def test_missing_api_key_fails_fast_without_any_request(service, monkeypatch):
    monkeypatch.setattr(module, "OPENAI_API_KEY", None)
    post = pytest.fail
    monkeypatch.setattr(module.requests, "post", lambda *args, **kwargs: post("no request expected"))
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        service.extract_play("https://cdn/slip.png")


def test_invalid_reads_cost_at_most_one_call_per_model(service, monkeypatch):
    calls = []
    monkeypatch.setattr(module.requests, "post",
                        lambda url, **kwargs: calls.append(kwargs["json"]["model"]) or response('{"units": 1, "legs": []}'))
    with pytest.raises(RuntimeError, match="All vision providers failed"):
        service.extract_play("https://cdn/slip.png")
    assert calls == ["model-a", "model-b"]


def test_auth_errors_are_not_retried_as_transient(service, monkeypatch):
    import requests as real_requests

    def rejected(*args, **kwargs):
        def raise_for_status():
            raise real_requests.HTTPError("401 Unauthorized")
        return SimpleNamespace(status_code=401, text="bad key", raise_for_status=raise_for_status)

    calls = []
    monkeypatch.setattr(module.requests, "post", lambda *args, **kwargs: calls.append(1) or rejected())
    with pytest.raises(RuntimeError, match="All vision providers failed"):
        service.extract_play("https://cdn/slip.png")
    assert len(calls) == 2


def test_every_call_reserves_budget_and_denial_stops_immediately(service, monkeypatch):
    monkeypatch.setattr(module, "OPENAI_VISION_DAILY_LIMIT", 50)
    granted = iter([True, False])
    rpc_calls = []

    class Rpc:
        def __init__(self, name, args):
            rpc_calls.append((name, args))

        def execute(self):
            return SimpleNamespace(data=next(granted))

    monkeypatch.setattr(module.supabase_service, "_ensure_client", lambda: SimpleNamespace(rpc=Rpc))
    posts = []
    monkeypatch.setattr(module.requests, "post",
                        lambda url, **kwargs: posts.append(1) or response('{"units": 1, "legs": []}'))
    with pytest.raises(module.VisionBudgetDenied, match="allowance is used up"):
        service.extract_play("https://cdn/slip.png")
    assert rpc_calls == [("reserve_openai_vision_request", {"p_daily_limit": 50})] * 2
    assert len(posts) == 1  # the denied second reservation made no provider call


def test_budget_outage_fails_closed(service, monkeypatch):
    monkeypatch.setattr(module, "OPENAI_VISION_DAILY_LIMIT", 50)

    def unavailable():
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be configured.")

    monkeypatch.setattr(module.supabase_service, "_ensure_client", unavailable)
    monkeypatch.setattr(module.requests, "post", lambda *args, **kwargs: pytest.fail("no request expected"))
    with pytest.raises(RuntimeError, match="All vision providers failed"):
        service.extract_play("https://cdn/slip.png")


def test_readable_text_ballot_still_parses(service, monkeypatch):
    monkeypatch.setattr(module.requests, "post", lambda url, **kwargs: response(
        '{"readable": true, "units": null, "legs": [{"selection": "Panthers ML", "odds": -150}]}'))
    assert service.extract_text_play("Panthers ml -150")["legs"] == [{"selection": "Panthers ML", "odds": -150}]