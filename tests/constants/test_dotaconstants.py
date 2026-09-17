import pytest
from constants.dotaconstants import fetch_heroes, fetch_items, derive_token_index

def _expect_count(what: str, got: int, want: int) -> None:
    """冻结计数是**流程事件**，不是测试事实。

    出现新英雄/新道具时正确动作是提升 constants_snapshot.snapshot_version 并重训
    （规格 §5.1：dense_index 稳定性靠冻结快照，不靠顺序假设），而不是改这条断言
    或手改 tests/fixtures/network/ 下的缓存。
    """
    if got != want:
        pytest.exit(
            f"{what} 计数变了：期望 {want}，实测 {got}。\n"
            f"常量快照是版本化的：请提升 constants_snapshot.snapshot_version 并重训模型；\n"
            f"若这是上游例行变化，用 REFRESH_NETWORK=1 重抓缓存后再核对 M1 的验收计数。\n"
            f"**不要**为了让测试变绿而改这条断言或手改缓存字节。",
            returncode=1,
        )

def test_hero_count_is_127():
    heroes = fetch_heroes()
    _expect_count("英雄", len(heroes), 127)
    # 对照组：英雄 payload 自带 name，不需要从字典键补（道具才需要）
    assert {h["id"]: h["name"] for h in heroes}[1] == "npc_dota_hero_antimage"

def test_item_count_is_501():
    items = fetch_items()
    _expect_count("道具", len(items), 501)
    # 短名（blink 等）只在字典键上——payload 里没有 key/name 字段，
    # 丢了它 Task 11 入库 items.name（TEXT NOT NULL UNIQUE）只能瞎猜。
    missing_key = [i.get("id") for i in items if not i.get("key")]
    assert missing_key == [], f"缺短名的 item_id: {missing_key}"
    assert next(i["key"] for i in items if i["id"] == 1) == "blink"   # Blink Dagger

def test_exactly_nine_hero_ids_exceed_126():
    ids = sorted(h["id"] for h in fetch_heroes())
    assert [i for i in ids if i > 126] == [128,129,131,135,136,137,138,145,155]

def test_token_index_is_dense_and_zero_based():
    m = derive_token_index(fetch_heroes())
    assert sorted(m.values()) == list(range(127))

def test_hero_135_maps_to_121():
    """规格 §16.2 用真实比赛验证过的具体映射。"""
    assert derive_token_index(fetch_heroes())[135] == 121

def test_ten_items_have_null_dname():
    assert sum(1 for i in fetch_items() if not i.get("dname")) == 10

def test_roles_vocabulary_is_the_measured_eight():
    """规格 §7 维度 6：词表只有 8 项，没有 Jungler。"""
    vocab = {r for h in fetch_heroes() for r in (h.get("roles") or [])}
    assert vocab == {"Carry","Disabler","Durable","Escape",
                     "Initiator","Nuker","Pusher","Support"}

def test_token_index_is_order_independent():
    """§5.1 的规则是「按 hero_id 升序排序后取下标」，不是「相信上游返回顺序」。

    live payload 恰好按 id 升序到达，只有打乱输入才能区分这两者。
    """
    assert derive_token_index([{"id": 155}, {"id": 1}, {"id": 128}]) == {1: 0, 128: 1, 155: 2}
    assert derive_token_index([]) == {}
