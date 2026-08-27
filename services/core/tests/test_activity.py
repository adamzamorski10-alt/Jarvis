"""Tests — Activity events."""
import pytest


# --- Service unit tests ---

@pytest.mark.asyncio
async def test_create_activity_event(activity_service, session):
    event = await activity_service.create_event(
        message="Test event",
        level="INFO",
        category="system",
    )
    await session.commit()
    assert event.id is not None
    assert event.message == "Test event"
    assert event.level == "INFO"
    assert event.category == "system"
    assert event.timestamp is not None


@pytest.mark.asyncio
async def test_create_event_with_details(activity_service, session):
    details = {"key": "value", "number": 42}
    event = await activity_service.create_event(
        message="Event with details",
        details=details,
    )
    await session.commit()
    assert event.details == details


@pytest.mark.asyncio
async def test_get_events_empty(activity_service):
    events, total = await activity_service.get_events()
    assert isinstance(events, list)
    assert total == 0


@pytest.mark.asyncio
async def test_get_events_returns_created(activity_service, session):
    await activity_service.create_event(message="Event A", category="api")
    await activity_service.create_event(message="Event B", category="system")
    await session.commit()

    events, total = await activity_service.get_events()
    assert total == 2
    assert len(events) == 2


@pytest.mark.asyncio
async def test_get_events_filter_by_category(activity_service, session):
    await activity_service.create_event(message="API event", category="api")
    await activity_service.create_event(message="System event", category="system")
    await session.commit()

    events, total = await activity_service.get_events(category="api")
    assert total == 1
    assert events[0].message == "API event"


@pytest.mark.asyncio
async def test_get_events_newest_first(activity_service, session):
    await activity_service.create_event(message="First")
    await activity_service.create_event(message="Second")
    await activity_service.create_event(message="Third")
    await session.commit()

    events, _ = await activity_service.get_events()
    assert events[0].message == "Third"
    assert events[-1].message == "First"


@pytest.mark.asyncio
async def test_get_events_pagination(activity_service, session):
    for i in range(10):
        await activity_service.create_event(message=f"Event {i}")
    await session.commit()

    page1, total = await activity_service.get_events(limit=5, offset=0)
    page2, _ = await activity_service.get_events(limit=5, offset=5)

    assert total == 10
    assert len(page1) == 5
    assert len(page2) == 5
    # Ensure no overlap
    ids1 = {e.id for e in page1}
    ids2 = {e.id for e in page2}
    assert ids1.isdisjoint(ids2)


# --- API integration tests ---

@pytest.mark.asyncio
async def test_get_activity_endpoint(client):
    response = await client.get("/api/activity")
    assert response.status_code == 200
    data = response.json()
    assert "events" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data


@pytest.mark.asyncio
async def test_post_activity_event(client):
    response = await client.post(
        "/api/activity",
        json={"message": "Test via API", "level": "INFO", "category": "api"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["message"] == "Test via API"
    assert data["id"] is not None


@pytest.mark.asyncio
async def test_activity_appears_in_get_after_post(client):
    await client.post(
        "/api/activity",
        json={"message": "Visible event", "category": "test"},
    )
    response = await client.get("/api/activity")
    events = response.json()["events"]
    messages = [e["message"] for e in events]
    assert "Visible event" in messages


@pytest.mark.asyncio
async def test_activity_filter_by_category_api(client):
    await client.post("/api/activity", json={"message": "API msg", "category": "api"})
    await client.post("/api/activity", json={"message": "System msg", "category": "system"})

    response = await client.get("/api/activity?category=api")
    events = response.json()["events"]
    assert all(e["category"] == "api" for e in events)
