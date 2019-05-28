import asyncio
import random
import sys

import pytest
from sentry_sdk import Hub, capture_message
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from sentry_asgi import SentryMiddleware


@pytest.fixture
def app():
    app = Starlette()

    @app.route("/sync-message")
    def hi(request):
        capture_message("hi", level="error")
        return PlainTextResponse("ok")

    @app.route("/async-message")
    async def hi(request):
        capture_message("hi", level="error")
        return PlainTextResponse("ok")

    app.add_middleware(SentryMiddleware)

    return app


@pytest.fixture
def app_with_extra_middleware():
    class _ExtraMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            sentry_hub: Hub = request.get("sentry_hub")
            if sentry_hub:
                with sentry_hub.configure_scope() as scope:
                    scope.user = {"id": "expected_user_id"}
            return await call_next(request)

    app = Starlette()

    app.add_middleware(_ExtraMiddleware)
    app.add_middleware(SentryMiddleware)

    return app


@pytest.mark.skipif(sys.version_info < (3, 7), reason="requires python3.7 or higher")
def test_sync_request_data(sentry_init, app, capture_events):
    sentry_init()
    events = capture_events()

    client = TestClient(app)
    response = client.get("/sync-message?foo=bar")

    assert response.status_code == 200

    event, = events
    assert event["transaction"] == "test_middleware.app.<locals>.hi"
    assert event["request"]["env"] == {"REMOTE_ADDR": "testclient"}
    assert set(event["request"]["headers"]) == {
        "accept",
        "accept-encoding",
        "connection",
        "host",
        "user-agent",
    }
    assert event["request"]["query_string"] == "foo=bar"
    assert event["request"]["url"].endswith("/sync-message")
    assert event["request"]["method"] == "GET"

    # Assert that state is not leaked
    events.clear()
    capture_message("foo")
    event, = events

    assert "request" not in event
    assert "transaction" not in event


def test_async_request_data(sentry_init, app, capture_events):
    sentry_init()
    events = capture_events()

    client = TestClient(app)
    response = client.get("/async-message?foo=bar")

    assert response.status_code == 200

    event, = events
    assert event["transaction"] == "test_middleware.app.<locals>.hi"
    assert event["request"]["env"] == {"REMOTE_ADDR": "testclient"}
    assert set(event["request"]["headers"]) == {
        "accept",
        "accept-encoding",
        "connection",
        "host",
        "user-agent",
    }
    assert event["request"]["query_string"] == "foo=bar"
    assert event["request"]["url"].endswith("/async-message")
    assert event["request"]["method"] == "GET"

    # Assert that state is not leaked
    events.clear()
    capture_message("foo")
    event, = events

    assert "request" not in event
    assert "transaction" not in event


def test_errors(sentry_init, app, capture_events):
    sentry_init()
    events = capture_events()

    @app.route("/error")
    def myerror(request):
        raise ValueError("oh no")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/error")

    assert response.status_code == 500

    event, = events
    assert event["transaction"] == "test_middleware.test_errors.<locals>.myerror"
    exception, = event["exception"]["values"]

    assert exception["type"] == "ValueError"
    assert exception["value"] == "oh no"
    assert any(
        frame["filename"].endswith("test_middleware.py")
        for frame in exception["stacktrace"]["frames"]
    )


def test_sentry_hub_is_set_in_context(
    sentry_init, app_with_extra_middleware, capture_events
):
    sentry_init()
    events = capture_events()

    @app_with_extra_middleware.route("/error")
    def myerror(request):
        raise ValueError("oh no")

    client = TestClient(app_with_extra_middleware, raise_server_exceptions=False)
    client.get("/error")

    event, = events

    assert event["user"]["id"] == "expected_user_id"
