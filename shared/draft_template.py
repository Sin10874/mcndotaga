"""规格 §6.0 / §8① 的 24 手 CM 模板。

**唯一定义处** —— 分析引擎、序列模型、契约校验器一律 import 这里。
两份副本必然漂移，而模板是本项目最不能出错的东西。

已验证（规格 §16.3）：OpenDota 全库 1,014 场中
`first_ban_team == first_pick_team` 成立 1014/1014。
"""
from __future__ import annotations

# (is_pick, 归属方)；'F' = 先手方, 'O' = 后手方
TEMPLATE: list[tuple[bool, str]] = [
    (False,'F'),(False,'F'),(False,'O'),(False,'O'),(False,'F'),(False,'O'),(False,'O'),  # 0-6   ban
    (True,'F'),(True,'O'),                                                                  # 7-8   pick
    (False,'F'),(False,'F'),(False,'O'),                                                    # 9-11  ban
    (True,'O'),(True,'F'),(True,'F'),(True,'O'),(True,'O'),(True,'F'),                      # 12-17 pick
    (False,'F'),(False,'O'),(False,'F'),(False,'O'),                                        # 18-21 ban
    (True,'F'),(True,'O'),                                                                  # 22-23 pick
]

def resolve(ord_: int, first_pick_team: int) -> tuple[bool, int]:
    """返回该手的 (is_pick, team)。team: 0=Radiant 1=Dire。

    手数与类型**不由模型预测**——由本模板 + first_pick_team 确定性推出。
    """
    if not 0 <= ord_ < len(TEMPLATE):
        raise ValueError(f"ord 必须在 0..23，收到 {ord_}")
    if first_pick_team not in (0, 1):
        raise ValueError(f"first_pick_team 必须是 0 或 1，收到 {first_pick_team}")
    is_pick, who = TEMPLATE[ord_]
    team = first_pick_team if who == "F" else 1 - first_pick_team
    return is_pick, team

def first_pick_team_from_actions(actions: list[dict]) -> int:
    """由 ord=0 的 team 推出先手方（规格 §8①）。"""
    for a in actions:
        if a["ord"] == 0:
            return int(a["team"])
    raise ValueError("actions 中缺少 ord=0，无法推出先手方")
