"""「什么时候才算数」的事实表。

prompt 与评测都要用同一份：角色卡分层测试（`tests/test_character_card_layers.py`）
检查 **prompt** 里不许提前出现这些词，探针评测（`scripts/eval_characters.py`）
检查 **模型回复** 里不许提前出现。两边共用一份，才不会各自漂移。

FUTURE_TERMS：角色 -> {术语: 最早成为事实的章回号}，逐条对着 db/seed.sql 的锚点状态核过。
OUT_OF_WORLD：角色是书中人，不该知道「全书」「原著」这回事。
  「小说」故意不在表里——「不要写成小说旁白」是给模型的出戏约束，本来就该在 prompt 里。
"""

from __future__ import annotations

from typing import Dict, Tuple

FUTURE_TERMS: Dict[str, Dict[str, int]] = {
    "song-jiang": {"招安": 71, "梁山泊主": 71},
    "lu-jun-yi": {"第二把交椅": 71, "赚上梁山": 71},
    "wu-yong": {"梁山军师": 19},
    "lin-chong": {"第六把交椅": 71, "马军五虎将": 71, "沧州": 10},
    "wu-song": {"鸳鸯楼": 31, "二龙山": 31, "步军头领": 71, "招安": 71},
    "lu-zhi-shen": {"步军头领": 71},
    "li-kui": {"步军头领": 71},
    "shi-jin": {"八骠骑": 71},
    "pan-jin-lian": {"毒杀": 31, "通奸": 31},
    "xi-men-qing": {"毒杀": 31, "私通": 31},
    "wang-po": {"毒杀": 31, "毒药": 31},
    "gao-qiu": {"构陷": 7},
}

OUT_OF_WORLD: Tuple[str, ...] = ("全书", "原著", "读者", "作者")
