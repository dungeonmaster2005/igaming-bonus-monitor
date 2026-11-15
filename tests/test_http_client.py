"""Captchas and geo-blocks served with HTTP 200 are blocks, not empty pages."""

import pytest
import requests

from src.http_client import BlockedError, PoliteClient, captcha_reason, decode, geo_block_reason

CAPTCHA_HTML = "<html><head><title>Just a moment...</title></head><body>Checking</body></html>"
OFFER_HTML = "<html><head><title>Unibet | Casino Welcome Offer</title></head><body>10x wagering</body></html>"


def _client_returning(monkeypatch, response):
    client = PoliteClient(delay_range=(0, 0))
    monkeypatch.setattr(client, "allowed", lambda url: True)
    monkeypatch.setattr(client.session, "get", lambda url, timeout: response)
    return client


def _response(status=200, url="https://casino.example/offer", text=OFFER_HTML):
    resp = requests.Response()
    resp.status_code, resp.url, resp._content = status, url, text.encode("utf-8")
    resp.headers["content-type"] = "text/html; charset=utf-8"
    return resp


def test_captcha_title_is_a_captcha():
    assert captcha_reason("https://casino.example/", CAPTCHA_HTML) == "page title 'Just a moment...'"


def test_normal_page_is_not_a_captcha_or_geo_block():
    assert captcha_reason("https://casino.example/offer", OFFER_HTML) is None
    assert geo_block_reason("https://casino.example/offer", OFFER_HTML) is None


def test_redirect_to_restricted_host_is_a_geo_block():
    # Casumo sends non-licensed regions to restricted.casumo.com.
    assert geo_block_reason("https://restricted.casumo.com", "<title>Restricted Country</title>")


@pytest.mark.parametrize("notice", [
    "We're sorry! Unfortunately, BetMGM isn't available in your country.",
    "Oops! This Website Isn't Available in Your Region",
    "Oops! MrQ Isn't in Your Area... yet!",
    "Tyvärr är LeoVegas inte tillgängligt i ditt land.",
    "Vi er desværre ikke tilgængelige i dit område!",
])
def test_geo_notice_anywhere_in_a_full_page_is_a_geo_block(notice):
    html = f"<html><body><main>{'Offer text. ' * 500}</main><div class='overlay'>{notice}</div></body></html>"
    assert geo_block_reason("https://casino.example/", html)


def test_vpn_clause_in_terms_is_not_a_geo_block():
    html = "<p>Using a VPN to bypass a provider's block is strictly prohibited.</p>"
    assert geo_block_reason("https://casino.example/terms", html) is None


def test_geo_notice_inside_script_is_ignored():
    html = "<script>var msg = \"isn't available in your country\";</script><p>Welcome offer</p>"
    assert geo_block_reason("https://casino.example/", html) is None


def test_client_raises_blocked_on_http_451(monkeypatch):
    client = _client_returning(monkeypatch, _response(status=451))
    with pytest.raises(BlockedError, match="451"):
        client.get_html("https://casino.example/offer")


def test_client_raises_blocked_on_geo_notice(monkeypatch):
    html = "<html><body>Unfortunately, BetMGM isn't available in your country.</body></html>"
    client = _client_returning(monkeypatch, _response(text=html))
    with pytest.raises(BlockedError, match="geo-blocked"):
        client.get_html("https://casino.example/")


def test_client_raises_blocked_on_captcha(monkeypatch):
    client = _client_returning(monkeypatch, _response(text=CAPTCHA_HTML))
    with pytest.raises(BlockedError, match="captcha"):
        client.get_html("https://casino.example/")


def test_client_returns_normal_page(monkeypatch):
    client = _client_returning(monkeypatch, _response())
    assert "10x wagering" in client.get_html("https://casino.example/offer")


def test_page_without_charset_is_decoded_as_utf8():
    resp = requests.Response()
    resp._content = "Deposit £10, get 100 free spins".encode("utf-8")
    resp.headers["content-type"] = "text/html"
    assert decode(resp) == "Deposit £10, get 100 free spins"


def test_meta_charset_wins_when_header_has_none():
    resp = requests.Response()
    resp._content = '<meta charset="iso-8859-1"><p>£10</p>'.encode("iso-8859-1")
    resp.headers["content-type"] = "text/html"
    assert "£10" in decode(resp)
