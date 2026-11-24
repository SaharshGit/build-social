"""Chat data questions: the read-only data tools, and the lookup -> grounded answer flow.
Fake LLM and a throwaway database: no API calls, no cost."""

import json
from datetime import datetime, timedelta, timezone

import yaml

from buildsocial.agents.chat import numbers_in
from buildsocial.chat_data import DataQuery, run_query
from buildsocial.db import brands as brand_ops
from buildsocial.db.models import Organization, Post, Run, RunStatus
from buildsocial.schemas import BrandProfile
from tests.test_api_runs import EXAMPLES
from tests.test_chat import decision, new_chat, say

PROFILE = yaml.safe_load((EXAMPLES / "brew-house.yaml").read_text())


async def seed(db):
    """Brew House: 2 pending + 1 approved posts (one LinkedIn, one 40 days old). Zerodha: 1 rejected.
    Another organization's post must never show up."""
    org = await brand_ops.default_org(db)
    brew = await brand_ops.create_brand(db, org, BrandProfile.model_validate(PROFILE))
    zerodha = await brand_ops.create_brand(db, org, BrandProfile.model_validate({**PROFILE, "name": "Zerodha"}))
    other_org = Organization(name="other")
    db.add(other_org)
    await db.flush()
    stranger = await brand_ops.create_brand(db, other_org, BrandProfile.model_validate({**PROFILE, "name": "Other"}))

    old = datetime.now(timezone.utc) - timedelta(days=40)
    for brand, status, platform, cost, created in [
        (brew, "pending", "instagram", 0.5, None),
        (brew, "pending", "linkedin", 0.25, None),
        (brew, "approved", "instagram", 0.75, old),
        (zerodha, "rejected", "instagram", 1.0, None),
        (stranger, "pending", "instagram", 9.0, None),
    ]:
        run = Run(brand_id=brand.id, brief={"topic": f"{brand.name} {status}"}, status=RunStatus.DONE,
                  cost={"INR": cost}, **({"created_at": created} if created else {}))
        db.add(run)
        await db.flush()
        db.add(Post(brand_id=brand.id, run_id=run.id, status=status, platform=platform, topic=f"{status} topic",
                    draft={}, review={"issues": []}, plan={}, **({"created_at": created} if created else {})))
    db.add(Run(brand_id=brew.id, brief={"topic": "broken"}, status=RunStatus.FAILED, error="LLMError: timeout"))
    await db.commit()
    return org


async def test_count_posts(db_maker):
    async with db_maker() as db:
        org = await seed(db)
        r = await run_query(db, org, DataQuery(tool="count_posts"))
        assert r["total"] == 4 and r["by_status"] == {"pending": 2, "approved": 1, "rejected": 1}  # not "Other"
        r = await run_query(db, org, DataQuery(tool="count_posts", status="pending", brand="brew house"))
        assert r["total"] == 2 and r["brand"] == "Brew House"
        assert (await run_query(db, org, DataQuery(tool="count_posts", platform="linkedin")))["total"] == 1
        assert (await run_query(db, org, DataQuery(tool="count_posts", days=7)))["total"] == 3


async def test_list_posts(db_maker):
    async with db_maker() as db:
        org = await seed(db)
        r = await run_query(db, org, DataQuery(tool="list_posts", status="pending"))
        assert r["shown"] == 2 and not r["more_exist"]
        assert {p["brand"] for p in r["posts"]} == {"Brew House"}


async def test_spend_and_runs(db_maker):
    async with db_maker() as db:
        org = await seed(db)
        r = await run_query(db, org, DataQuery(tool="spend"))
        assert r["total_cost"] == {"INR": 2.5}  # the other organization's 9.0 isn't counted
        assert r["per_brand"] == {"Brew House": {"INR": 1.5}, "Zerodha": {"INR": 1.0}}
        r = await run_query(db, org, DataQuery(tool="run_stats", brand="Brew House"))
        assert r["total"] == 4 and r["by_status"]["failed"] == 1
        assert r["recent_failures"][0]["error"] == "LLMError: timeout"


async def test_brand_info_and_unknown_brand(db_maker):
    async with db_maker() as db:
        org = await seed(db)
        r = await run_query(db, org, DataQuery(tool="brand_info", brand="zerodha"))
        assert r["name"] == "Zerodha" and r["posts_by_status"] == {"rejected": 1}
        r = await run_query(db, org, DataQuery(tool="count_posts", brand="Other"))  # another org's brand
        assert "error" in r and "Other" not in r["brands"]


def test_numbers_in():
    assert numbers_in("1,200 posts cost ₹0.50 on 2026-11-24") == {"1200", "0.5", "2026", "11", "24"}


# --- the chat flow ----------------------------------------------------------------------

def lookup(**query):
    return decision("lookup", "Let me check.", **query)


async def test_lookup_answers_from_the_data(client, chat, db_maker):
    async with db_maker() as db:
        await seed(db)
    conv = await new_chat(client)
    chat(lookup(tool="count_posts", status="pending"),
         json.dumps({"reply": "You have 2 pending posts, both for Brew House."}))
    turn = await say(client, conv, "how many posts are pending?")
    assert turn["assistant"]["content"] == "You have 2 pending posts, both for Brew House."
    card = turn["assistant"]["attachments"][0]
    assert card["type"] == "data" and card["query"]["tool"] == "count_posts" and card["result"]["total"] == 2


async def test_invented_numbers_are_rejected(client, chat, db_maker):
    async with db_maker() as db:
        await seed(db)
    conv = await new_chat(client)
    q = chat(lookup(tool="spend"),
             json.dumps({"reply": "You spent about ₹3 so far."}),  # 3 isn't in the data -> sent back
             json.dumps({"reply": "You spent ₹2.5 so far."}))
    turn = await say(client, conv, "what did we spend?")
    assert turn["assistant"]["content"] == "You spent ₹2.5 so far." and q == []


async def test_if_the_answer_keeps_failing_the_data_is_shown(client, chat, db_maker):
    async with db_maker() as db:
        await seed(db)
    conv = await new_chat(client)
    bad = json.dumps({"reply": "About 99 posts."})
    chat(lookup(tool="count_posts"), bad, bad, bad)
    turn = await say(client, conv, "how many posts?")
    assert turn["assistant"]["content"] == "Here's what I found:"
    assert turn["assistant"]["attachments"][0]["result"]["total"] == 4


async def test_lookup_with_unknown_brand_goes_back_to_the_model(client, chat, db_maker):
    async with db_maker() as db:
        await seed(db)
    conv = await new_chat(client)
    chat(lookup(tool="count_posts", brand="Nykaa"), decision("clarify", "I don't have Nykaa. Brew House or Zerodha?"))
    turn = await say(client, conv, "how many nykaa posts")
    assert "Nykaa" in turn["assistant"]["content"] and turn["assistant"]["attachments"] == []


async def test_brand_info_uses_the_chat_brand(client, chat, db_maker):
    async with db_maker() as db:
        await seed(db)
        brew = next(b for b in await brand_ops.list_brands(db, await brand_ops.default_org(db))
                    if b.name == "Brew House")
    conv = await new_chat(client, str(brew.id))
    chat(lookup(tool="brand_info"), json.dumps({"reply": "Brew House is a specialty coffee cafe."}))
    turn = await say(client, conv, "remind me what our brand is about")
    assert turn["assistant"]["attachments"][0]["result"]["name"] == "Brew House"
