"""Tests proving docs/contracts/retriever.md's stated behaviors -- not just
"it works", but the specific contractual guarantees: idempotent register,
no-op deregister on unknown, optional kind search, rank-not-score ordering,
automatic fallback on primary failure.
"""

from __future__ import annotations

from typing import Literal

import pytest

from fabrica.retriever import (
    DuplicateIndexableError,
    Indexable,
    KeywordBackend,
    RankedMatch,
    Retriever,
    RetrieverUnavailableError,
)


def make_tool(id_: str, name: str, description: str, eager: bool = False) -> Indexable:
    return Indexable(id=id_, kind="tool", name=name, description=description, eager=eager)


@pytest.fixture
def retriever() -> Retriever:
    return Retriever(primary=KeywordBackend())


async def test_register_then_search_finds_it(retriever: Retriever) -> None:
    item = make_tool("t1", "send_email", "send an email to a recipient")
    await retriever.register([item])

    results = await retriever.search("email")

    assert len(results) == 1
    assert results[0].item.id == "t1"
    assert results[0].rank == 0


async def test_register_is_idempotent_for_identical_content(retriever: Retriever) -> None:
    item = make_tool("t1", "send_email", "send an email to a recipient")
    await retriever.register([item])
    # Re-registering identical content must NOT raise -- a service restart
    # re-registering its own tool set must not error.
    await retriever.register([item])

    results = await retriever.search("email")
    assert len(results) == 1  # not duplicated


async def test_register_raises_on_id_collision_with_different_content(
    retriever: Retriever,
) -> None:
    await retriever.register([make_tool("t1", "send_email", "send an email")])

    with pytest.raises(DuplicateIndexableError):
        await retriever.register([make_tool("t1", "send_email", "a completely different tool")])


async def test_register_allows_eager_flag_update_without_raising(retriever: Retriever) -> None:
    # eager is NOT an identity-bearing field per the contract -- only
    # kind/name/description are. Toggling it is a legitimate update.
    await retriever.register([make_tool("t1", "send_email", "send an email", eager=False)])
    await retriever.register([make_tool("t1", "send_email", "send an email", eager=True)])

    eager_items = await retriever.list_eager()
    assert len(eager_items) == 1
    assert eager_items[0].id == "t1"


async def test_deregister_unknown_id_is_a_noop(retriever: Retriever) -> None:
    # Must not raise -- callers should not need to track what's already
    # registered to safely call this.
    await retriever.deregister(["never-registered"])


async def test_deregister_removes_from_search_results(retriever: Retriever) -> None:
    await retriever.register([make_tool("t1", "send_email", "send an email")])
    await retriever.deregister(["t1"])

    results = await retriever.search("email")
    assert results == []


async def test_search_returns_empty_list_not_error_when_nothing_matches(
    retriever: Retriever,
) -> None:
    results = await retriever.search("nonexistent query against empty index")
    assert results == []


async def test_search_kind_none_searches_both_tools_and_skills(retriever: Retriever) -> None:
    tool = make_tool("t1", "send_email", "send an email message")
    skill = Indexable(
        id="s1", kind="skill", name="email-drafting", description="draft an email message"
    )
    await retriever.register([tool, skill])

    results = await retriever.search("email message", kind=None)

    kinds = {r.item.kind for r in results}
    assert kinds == {"tool", "skill"}


async def test_search_kind_filter_restricts_to_that_kind(retriever: Retriever) -> None:
    tool = make_tool("t1", "send_email", "send an email message")
    skill = Indexable(
        id="s1", kind="skill", name="email-drafting", description="draft an email message"
    )
    await retriever.register([tool, skill])

    results = await retriever.search("email message", kind="tool")

    assert all(r.item.kind == "tool" for r in results)
    assert len(results) == 1


async def test_search_results_are_pre_sorted_ascending_by_rank(retriever: Retriever) -> None:
    await retriever.register(
        [
            make_tool("t1", "send_email", "send an email message to someone"),
            make_tool("t2", "read_email", "read the latest email"),
            make_tool("t3", "unrelated", "completely unrelated tool about weather"),
        ]
    )

    results = await retriever.search("email message")

    ranks = [r.rank for r in results]
    assert ranks == sorted(ranks)
    assert ranks[0] == 0


async def test_rankedmatch_has_no_score_field() -> None:
    # Enforced by the type itself, not just a docstring -- rank-not-threshold.
    item = make_tool("t1", "x", "y")
    match = RankedMatch(item=item, rank=0)
    assert not hasattr(match, "score")


async def test_list_eager_returns_only_eager_items(retriever: Retriever) -> None:
    await retriever.register(
        [
            make_tool("t1", "eager_tool", "always visible", eager=True),
            make_tool("t2", "hidden_tool", "behind search", eager=False),
        ]
    )

    eager = await retriever.list_eager()

    assert [item.id for item in eager] == ["t1"]


async def test_list_eager_filters_by_kind(retriever: Retriever) -> None:
    await retriever.register(
        [
            make_tool("t1", "eager_tool", "always visible", eager=True),
            Indexable(
                id="s1", kind="skill", name="eager_skill", description="also eager", eager=True
            ),
        ]
    )

    tools_only = await retriever.list_eager(kind="tool")

    assert [item.id for item in tools_only] == ["t1"]


async def test_list_eager_reflects_deregister_immediately_even_if_backend_removal_fails(
    retriever: Retriever,
) -> None:
    # contracts/retriever.md open item 1: list_eager()'s cache must not
    # show a deregistered eager item during the (or after a failed)
    # backend-removal window -- deregister()'s intent takes effect
    # immediately, not once best-effort backend cleanup happens to finish.
    retriever = Retriever(primary=_AlwaysFailsBackend())
    await retriever.register([make_tool("t1", "eager_tool", "always visible", eager=True)])
    assert [item.id for item in await retriever.list_eager()] == ["t1"]

    await retriever.deregister(["t1"])  # both backends' remove() raise, best-effort swallows it

    assert await retriever.list_eager() == []


async def test_register_batch_is_all_or_nothing_on_duplicate_conflict(
    retriever: Retriever,
) -> None:
    # contracts/retriever.md open item 2: one bad item in a batch must not
    # let the rest of that same batch land -- a real all-or-nothing
    # guarantee for the duplicate-id check specifically, decided in favor
    # of best-effort only past that point (see register()'s own docstring).
    await retriever.register([make_tool("t1", "send_email", "send an email")])

    with pytest.raises(DuplicateIndexableError):
        await retriever.register(
            [
                make_tool("t2", "brand_new_tool", "a genuinely new tool", eager=True),
                make_tool("t1", "send_email", "a conflicting redefinition"),
            ]
        )

    # t2 must NOT have landed even though it was a valid, non-conflicting
    # item in the same batch -- the duplicate check runs over the whole
    # list before either backend is touched. Checked via list_eager(),
    # not search() -- BM25 over a tiny corpus can return non-zero scores
    # for unrelated documents, making "absent from search results" an
    # unreliable assertion here; list_eager() is a deterministic
    # membership check against Retriever's own bookkeeping.
    assert await retriever.list_eager() == []


class _AlwaysFailsBackend:
    """A RetrieverBackend that fails every call -- for testing fallback."""

    async def add(self, items: list[Indexable]) -> None:
        raise RuntimeError("primary is down")

    async def remove(self, ids: list[str]) -> None:
        raise RuntimeError("primary is down")

    async def query(
        self, query: str, kind: Literal["tool", "skill"] | None, limit: int
    ) -> list[RankedMatch]:
        raise RuntimeError("primary is down")

    async def health_check(self) -> bool:
        return False


async def test_search_falls_back_automatically_on_primary_failure() -> None:
    retriever = Retriever(primary=_AlwaysFailsBackend())
    # register() writes to both backends; primary fails, fallback succeeds --
    # this must not raise, per the dual-write resolution.
    await retriever.register([make_tool("t1", "send_email", "send an email message")])

    # search() must transparently fall back and still find the item.
    results = await retriever.search("email message")

    assert len(results) == 1
    assert results[0].item.id == "t1"


async def test_search_raises_unavailable_when_both_backends_fail() -> None:
    class _AlwaysFailsFallback(_AlwaysFailsBackend):
        pass

    retriever = Retriever(primary=_AlwaysFailsBackend(), fallback=_AlwaysFailsFallback())

    with pytest.raises(RetrieverUnavailableError):
        await retriever.search("anything")


async def test_indexable_rejects_empty_id() -> None:
    with pytest.raises(ValueError):
        Indexable(id="", kind="tool", name="x", description="y")
