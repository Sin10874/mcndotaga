# `tests/fixtures/network/` —— 上游响应的冻结缓存

测试默认**离线**：这些文件就是 `constants/_http.py::fetch_json` 的本地缓存，
命中即返回，不碰网络（同时不碰真实缓存目录的写路径）。

## 内容与来源（抓取日期：2026-09-17）

| 文件 | 上游 URL | 大小 | sha256 |
|---|---|---|---|
| `opendota_heroes.json` | `https://api.opendota.com/api/constants/heroes` | 92569 B | `5fe8f41250b5f705232f58d3238eaf89720e554958191ba3f323187803b73650` |
| `opendota_items.json` | `https://api.opendota.com/api/constants/items` | 344486 B | `d814ba42282c9423780a780fd45b4bda0a732e0e5266700231febc4ac7f670d9` |

两文件都是**上游原始字节**（`path.write_bytes(resp.content)`，参见 `_http.py`），
不是重新序列化的 `json.dumps`——因此"当前提交是否等于上游"可以直接逐字节比对。

## 约定

- **提交进仓库**：测试因此完全离线、不受上游时间点漂移影响（本计划 Chunk 3 的离线策略）。
- **不得手工编辑**。这两个文件是"上游在某一天长什么样"的证据；手改会让常量测试
  与 M1 的入库计数一起说谎。要更新只能整份重新抓取（见下）。
- 断言里那些**冻结计数**（127 英雄 / 501 道具 / 十项 `dname` 为空）是**流程事件**：
  出现新英雄/新道具时，正确动作是提升 `constants_snapshot.snapshot_version` 并重训，
  而不是改测试或改缓存。计数测试会以 `pytest.exit` 给出这条指引。

## 刷新（需要网络）

```bash
# 重新抓取两个文件（覆盖上面的缓存，随后请一并核对大小/sha 再提交）
REFRESH_NETWORK=1 .venv/bin/pytest tests/constants -q

# 只想要抓取、不想跑测试：
REFRESH_NETWORK=1 .venv/bin/python -c \
  "from constants.dotaconstants import fetch_heroes, fetch_items; fetch_heroes(); fetch_items()"
```

`REFRESH_NETWORK` 采用**严格解析**：只有 `1` / `true` / `yes`（忽略大小写与空白）
表示刷新，`""` / `0` / `false` / `no` 一律是"用缓存"。

抓取会写进 `tests/fixtures/network/`；若该目录只读，可用 `MCNDOTAGA_CACHE_DIR`
指向别处（默认不变）：

```bash
MCNDOTAGA_CACHE_DIR=/tmp/mcndotaga-cache .venv/bin/pytest tests/constants -q
```

## 与网络相关的测试

`tests/constants/test_http.py` 用 `tmp_path` + `monkeypatch` 覆盖缓存命中/刷新/坏缓存
三条语义，**既不读本目录也不打网络**——所以本目录的文件被改坏时，是常量测试而不是
HTTP 测试报错，且错误信息会点名文件与两种恢复方式。

> 尚未纳入本缓存：`constants/patches.py` 要用的
> `https://www.dota2.com/datafeed/patchnoteslist?language=english` 与
> `D2-LRG-Metadata/patchdates.json`（Task 10 的范围）。
