"""Tests for the Usenet-inspired newsgroup system."""

import os
import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import store as content_store
from database import (
    init_db, close_db, create_user,
    create_newsgroup, post_article, get_article,
    list_newsgroups, get_newsgroup, delete_newsgroup,
    list_articles, list_thread, build_thread_tree, get_thread_roots,
    check_spam,
    subscribe_newsgroup, unsubscribe_newsgroup,
    list_subscriptions, list_group_subscribers,
)


@pytest.fixture(autouse=True)
def _load_content():
    content_store.load()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset rate limiter between tests to avoid cross-test rate limit hits."""
    from app import limiter
    limiter.reset()
    yield


@pytest.fixture(autouse=True)
async def _init_test_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "test.db")
    import database
    database.DB_PATH = str(tmp_path / "test.db")
    await init_db()
    yield
    await close_db()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin_client(client):
    """Client logged in as admin."""
    await create_user("admin", "admin@test.com", "pass123", role="admin")
    await client.post("/login", data={"username": "admin", "password": "pass123"})
    return client


@pytest.fixture
async def user_client(client):
    """Client logged in as regular user."""
    await create_user("testuser", "test@test.com", "pass123")
    await client.post("/login", data={"username": "testuser", "password": "pass123"})
    return client


# ── Database layer tests ─────────────────────────────────────────


@pytest.mark.anyio
async def test_create_newsgroup():
    group = await create_newsgroup("izabael.test", "A test group", "Be nice.")
    assert group is not None
    assert group["name"] == "izabael.test"
    assert group["description"] == "A test group"
    assert group["article_count"] == 0


@pytest.mark.anyio
async def test_create_duplicate_newsgroup():
    await create_newsgroup("izabael.test", "First")
    dup = await create_newsgroup("izabael.test", "Second")
    assert dup is None


@pytest.mark.anyio
async def test_list_newsgroups():
    await create_newsgroup("izabael.alpha", "Alpha")
    await create_newsgroup("izabael.beta", "Beta")
    groups = await list_newsgroups()
    names = [g["name"] for g in groups]
    assert "izabael.alpha" in names
    assert "izabael.beta" in names


@pytest.mark.anyio
async def test_delete_newsgroup():
    await create_newsgroup("izabael.doomed")
    deleted = await delete_newsgroup("izabael.doomed")
    assert deleted is True
    assert await get_newsgroup("izabael.doomed") is None


@pytest.mark.anyio
async def test_post_article():
    await create_newsgroup("izabael.test")
    art = await post_article("izabael.test", "Hello World", "First post!", "TestBot")
    assert art["message_id"].startswith("<")
    assert art["message_id"].endswith(">")
    assert art["subject"] == "Hello World"
    assert art["depth"] == 0
    assert art["in_reply_to"] == ""


@pytest.mark.anyio
async def test_threaded_reply():
    await create_newsgroup("izabael.test")
    root = await post_article("izabael.test", "Root", "Root body", "Bot1")
    reply = await post_article(
        "izabael.test", "Re: Root", "Reply body", "Bot2",
        in_reply_to=root["message_id"],
    )
    assert reply["depth"] == 1
    assert reply["in_reply_to"] == root["message_id"]
    assert root["message_id"] in reply["ref_chain"]


@pytest.mark.anyio
async def test_deep_threading():
    await create_newsgroup("izabael.test")
    root = await post_article("izabael.test", "Root", "body", "A")
    r1 = await post_article("izabael.test", "Re: Root", "body", "B",
                            in_reply_to=root["message_id"])
    r2 = await post_article("izabael.test", "Re: Re: Root", "body", "C",
                            in_reply_to=r1["message_id"])
    assert r2["depth"] == 2
    assert root["message_id"] in r2["ref_chain"]
    assert r1["message_id"] in r2["ref_chain"]


@pytest.mark.anyio
async def test_list_thread():
    await create_newsgroup("izabael.test")
    root = await post_article("izabael.test", "Root", "body", "A")
    await post_article("izabael.test", "Re: Root", "reply", "B",
                       in_reply_to=root["message_id"])
    await post_article("izabael.test", "Unrelated", "other", "C")

    thread = await list_thread(root["message_id"])
    assert len(thread) == 2  # root + reply


@pytest.mark.anyio
async def test_build_thread_tree():
    await create_newsgroup("izabael.test")
    root = await post_article("izabael.test", "Root", "body", "A")
    r1 = await post_article("izabael.test", "Re: Root", "reply", "B",
                            in_reply_to=root["message_id"])
    await post_article("izabael.test", "Re: Re: Root", "deep", "C",
                       in_reply_to=r1["message_id"])

    thread = await list_thread(root["message_id"])
    tree = build_thread_tree(thread)
    assert len(tree) == 1  # one root
    assert len(tree[0]["children"]) == 1  # one reply
    assert len(tree[0]["children"][0]["children"]) == 1  # one nested reply


@pytest.mark.anyio
async def test_get_thread_roots():
    await create_newsgroup("izabael.test")
    root1 = await post_article("izabael.test", "Thread 1", "body", "A")
    root2 = await post_article("izabael.test", "Thread 2", "body", "B")
    await post_article("izabael.test", "Re: Thread 1", "reply", "C",
                       in_reply_to=root1["message_id"])

    roots = await get_thread_roots("izabael.test")
    assert len(roots) == 2
    # Most recent first
    assert roots[0]["subject"] == "Thread 2"
    # root1 should have reply_count = 1
    root1_entry = next(r for r in roots if r["message_id"] == root1["message_id"])
    assert root1_entry["reply_count"] == 1


@pytest.mark.anyio
async def test_article_count_updates():
    await create_newsgroup("izabael.test")
    await post_article("izabael.test", "Post 1", "body", "A")
    await post_article("izabael.test", "Post 2", "body", "B")
    group = await get_newsgroup("izabael.test")
    assert group["article_count"] == 2
    assert group["last_post"] is not None


# ── Subscription tests ───────────────────────────────────────────


@pytest.mark.anyio
async def test_subscribe_unsubscribe():
    await create_newsgroup("izabael.test")
    assert await subscribe_newsgroup("agent1", "izabael.test") is True
    assert await subscribe_newsgroup("agent1", "izabael.test") is False  # already subscribed

    subs = await list_subscriptions("agent1")
    assert "izabael.test" in subs

    subscribers = await list_group_subscribers("izabael.test")
    assert "agent1" in subscribers

    assert await unsubscribe_newsgroup("agent1", "izabael.test") is True
    assert await unsubscribe_newsgroup("agent1", "izabael.test") is False  # not subscribed


# ── API tests ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_api_list_newsgroups(client):
    await create_newsgroup("izabael.test", "Test group")
    resp = await client.get("/api/newsgroups")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["groups"]) >= 1


@pytest.mark.anyio
async def test_api_create_newsgroup_requires_auth(client):
    resp = await client.post("/api/newsgroups", json={
        "name": "izabael.test", "description": "Test",
    })
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_api_create_newsgroup_admin(admin_client):
    resp = await admin_client.post("/api/newsgroups", json={
        "name": "izabael.admin.test", "description": "Admin created",
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.anyio
async def test_api_post_article_requires_auth(client):
    await create_newsgroup("izabael.test")
    resp = await client.post("/api/newsgroups/izabael.test/articles", json={
        "subject": "Test", "body": "Hello",
    })
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_api_post_article_logged_in(user_client):
    await create_newsgroup("izabael.test")
    resp = await user_client.post("/api/newsgroups/izabael.test/articles", json={
        "subject": "My First Post", "body": "Hello from the parlor!",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["article"]["subject"] == "My First Post"


@pytest.mark.anyio
async def test_api_post_reply(user_client):
    await create_newsgroup("izabael.test")
    # Post root
    resp = await user_client.post("/api/newsgroups/izabael.test/articles", json={
        "subject": "Root", "body": "Start a thread",
    })
    root_id = resp.json()["article"]["message_id"]
    # Post reply
    resp2 = await user_client.post("/api/newsgroups/izabael.test/articles", json={
        "subject": "Re: Root", "body": "A reply", "in_reply_to": root_id,
    })
    assert resp2.status_code == 200
    assert resp2.json()["article"]["depth"] == 1


@pytest.mark.anyio
async def test_api_get_thread(user_client):
    await create_newsgroup("izabael.test")
    resp = await user_client.post("/api/newsgroups/izabael.test/articles", json={
        "subject": "Root", "body": "Thread start",
    })
    root_id = resp.json()["article"]["message_id"]
    await user_client.post("/api/newsgroups/izabael.test/articles", json={
        "subject": "Re: Root", "body": "Reply", "in_reply_to": root_id,
    })

    resp = await user_client.get(
        f"/api/newsgroups/izabael.test/thread/{root_id}"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["article_count"] == 2
    assert len(data["thread"]) == 1  # one root in tree
    assert len(data["thread"][0]["children"]) == 1


@pytest.mark.anyio
async def test_api_post_to_nonexistent_group(user_client):
    resp = await user_client.post("/api/newsgroups/izabael.fake/articles", json={
        "subject": "Test", "body": "Hello",
    })
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_api_reply_to_nonexistent_parent(user_client):
    await create_newsgroup("izabael.test")
    resp = await user_client.post("/api/newsgroups/izabael.test/articles", json={
        "subject": "Re: ???", "body": "Orphan", "in_reply_to": "<fake@izabael.com>",
    })
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_api_delete_newsgroup_requires_admin(user_client):
    await create_newsgroup("izabael.doomed")
    resp = await user_client.request("DELETE", "/api/newsgroups/izabael.doomed")
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_api_delete_newsgroup_admin(admin_client):
    await create_newsgroup("izabael.doomed")
    resp = await admin_client.request("DELETE", "/api/newsgroups/izabael.doomed")
    assert resp.status_code == 200


# ── Page rendering tests ─────────────────────────────────────────


@pytest.mark.anyio
async def test_newsgroups_page_renders(client):
    resp = await client.get("/newsgroups")
    assert resp.status_code == 200
    assert "IZABAEL NEWSGROUPS" in resp.text


@pytest.mark.anyio
async def test_newsgroups_group_page(client):
    await create_newsgroup("izabael.test", "A test group")
    resp = await client.get("/newsgroups/izabael.test")
    assert resp.status_code == 200
    assert "izabael.test" in resp.text


@pytest.mark.anyio
async def test_newsgroups_thread_page(client):
    await create_newsgroup("izabael.test")
    art = await post_article("izabael.test", "Hello", "Body text", "TestBot")
    resp = await client.get(f"/newsgroups/izabael.test/thread/{art['message_id']}")
    assert resp.status_code == 200
    assert "Hello" in resp.text
    assert "Body text" in resp.text


@pytest.mark.anyio
async def test_newsgroups_404_group(client):
    resp = await client.get("/newsgroups/izabael.nonexistent")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_newsgroups_404_thread(client):
    await create_newsgroup("izabael.test")
    resp = await client.get("/newsgroups/izabael.test/thread/<fake@izabael.com>")
    assert resp.status_code == 404


# ── Spam guard tests ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_spam_body_too_short():
    await create_newsgroup("izabael.test")
    reason = await check_spam("izabael.test", "Bot", "Hi", "x")
    assert reason is not None
    assert "too short" in reason


@pytest.mark.anyio
async def test_spam_allows_normal_post():
    await create_newsgroup("izabael.test")
    reason = await check_spam("izabael.test", "Bot", "Hello", "A normal post body")
    assert reason is None


@pytest.mark.anyio
async def test_spam_duplicate_detection():
    await create_newsgroup("izabael.test")
    await post_article("izabael.test", "Hello", "Same body here", "SpamBot")
    reason = await check_spam("izabael.test", "SpamBot", "Hello again", "Same body here")
    assert reason is not None
    assert "Duplicate" in reason


@pytest.mark.anyio
async def test_spam_different_body_ok():
    await create_newsgroup("izabael.test")
    await post_article("izabael.test", "Hello", "First post body", "Bot")
    reason = await check_spam("izabael.test", "Bot", "Hello", "Different body entirely")
    assert reason is None


@pytest.mark.anyio
async def test_spam_flood_protection():
    await create_newsgroup("izabael.test")
    for i in range(5):
        await post_article("izabael.test", f"Post {i}", f"Body number {i}", "FloodBot")
    reason = await check_spam("izabael.test", "FloodBot", "One more", "Flood attempt")
    assert reason is not None
    assert "too many posts" in reason.lower()


@pytest.mark.anyio
async def test_spam_flood_different_author_ok():
    await create_newsgroup("izabael.test")
    for i in range(5):
        await post_article("izabael.test", f"Post {i}", f"Body {i}", "FloodBot")
    # Different author should be fine
    reason = await check_spam("izabael.test", "GoodBot", "Hello", "I'm different")
    assert reason is None


@pytest.mark.anyio
async def test_spam_crosspost_flood():
    for g in ["izabael.one", "izabael.two", "izabael.three"]:
        await create_newsgroup(g)
    for g in ["izabael.one", "izabael.two", "izabael.three"]:
        await post_article(g, "BUY CRYPTO", f"Body in {g}", "SpamBot")
    await create_newsgroup("izabael.four")
    reason = await check_spam("izabael.four", "SpamBot", "BUY CRYPTO", "More spam")
    assert reason is not None
    assert "Crosspost" in reason


@pytest.mark.anyio
async def test_spam_api_returns_429(user_client):
    await create_newsgroup("izabael.test")
    # Post once normally
    resp = await user_client.post("/api/newsgroups/izabael.test/articles", json={
        "subject": "Hello", "body": "Original post",
    })
    assert resp.status_code == 200
    # Post duplicate — should get 429
    resp = await user_client.post("/api/newsgroups/izabael.test/articles", json={
        "subject": "Hello again", "body": "Original post",
    })
    assert resp.status_code == 429
    assert "Duplicate" in resp.json()["detail"]
