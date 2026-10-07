import pytest
from pydantic import ValidationError

from app.api.schemas.web_search import WebSearchConfigRequest


def test_web_search_config_rejects_blank_api_key():
    with pytest.raises(ValidationError):
        WebSearchConfigRequest(provider="brave", api_key="   ")


def test_web_search_config_normalizes_provider_and_key():
    value = WebSearchConfigRequest(provider=" BRAVE ", api_key="  secret  ")
    assert value.provider == "brave"
    assert value.api_key == "secret"


def test_web_search_config_forbids_extra_fields():
    with pytest.raises(ValidationError):
        WebSearchConfigRequest(provider="brave", api_key="secret", secret="other")


def test_web_search_config_does_not_accept_empty_provider():
    with pytest.raises(ValidationError):
        WebSearchConfigRequest(provider="   ")
