# `tests/fixtures/network/` —— 上游响应的冻结缓存

测试默认**离线**：这些文件就是 `constants/_http.py::fetch_json` 的本地缓存，
命中即返回，不碰网络（同时不碰真实缓存目录的写路径）。

## 内容与来源（抓取日期：2026-09-17）

| 文件 | 上游 URL | 大小 | sha256 |
|---|---|---|---|
| `opendota_heroes.json` | `https://api.opendota.com/api/constants/heroes` | 92569 B | `5fe8f41250b5f705232f58d3238eaf89720e554958191ba3f323187803b73650` |
| `opendota_items.json` | `https://api.opendota.com/api/constants/items` | 344486 B | `d814ba42282c9423780a780fd45b4bda0a732e0e5266700231febc4ac7f670d9` |
| `dota2_patchnoteslist.json` | `https://www.dota2.com/datafeed/patchnoteslist?language=english` | 9129 B | `9ec57bde0c37aee9b782bd7190cc0c18b68310d90b6cba0b9f9dd3b196bd29ea` |
| `d2lrg_patchdates.json` | `https://raw.githubusercontent.com/leamare/D2-LRG-Metadata/master/patchdates.json` | 7686 B | `5c54af61aea33e8b84c63bc77bf144661419f5453cbd93bc864ae2a8c26c89e4` |

四文件都是**上游原始字节**（`path.write_bytes(resp.content)`，参见 `_http.py`），
不是重新序列化的 `json.dumps`——因此"当前提交是否等于上游"可以直接逐字节比对。

### `patchdates` 的 raw → jsDelivr 回退（2026-09-17 实测）

canonical URL 是 raw.githubusercontent.com 那条（`constants/patches.py::PATCHDATES_URL`）。
抓取当天本环境**连不上 raw**：TLS 握手超时（curl 与 httpx 都试过，20–60 s 无响应，
`httpx.ConnectTimeout: _ssl.c:1063: The handshake operation timed out`），
而 jsDelivr 镜像 0.9 s 返回 200 / **7686 B**。`fetch_patchdates()` 因此实现了
「raw 优先、抛 `httpx.HTTPError` 时回退镜像」；上表的 sha256 是**镜像**给出的字节，
与 raw 的同名文件是同一份仓库内容（镜像只做 CDN 缓存，不改写文件）。
若将来 raw 可达，`REFRESH_NETWORK=1` 重新抓取后请核对 sha256 是否仍是 `5c54af61…`。

## 约定

- **提交进仓库**：测试因此完全离线、不受上游时间点漂移影响（本计划 Chunk 3 的离线策略）。
- **不得手工编辑**。这些文件是"上游在某一天长什么样"的证据；手改会让常量测试
  与 M1 的入库计数一起说谎。要更新只能整份重新抓取（见下）。
- 断言里那些**冻结计数**（127 英雄 / 501 道具 / 十项 `dname` 为空 / 118 版本 / 84 字母版本 /
  `patchdates` 141 个字母槽）是**流程事件**：出现新英雄/新道具/新版本时，正确动作是提升
  `constants_snapshot.snapshot_version` 并重训，而不是改测试或改缓存。计数测试会以
  `pytest.exit` 给出这条指引。

## 刷新（需要网络）

```bash
# 重新抓取全部四个文件（覆盖上面的缓存，随后请一并核对大小/sha 再提交）
REFRESH_NETWORK=1 .venv/bin/pytest tests/constants -q

# 只想要抓取、不想跑测试：
REFRESH_NETWORK=1 .venv/bin/python -c \
  "from constants.dotaconstants import fetch_heroes, fetch_items; fetch_heroes(); fetch_items()"
REFRESH_NETWORK=1 .venv/bin/python -c \
  "from constants.patches import fetch_valve_patches, fetch_patchdates; fetch_valve_patches(); fetch_patchdates()"
```

`REFRESH_NETWORK` 采用**严格解析**：只有 `1` / `true` / `yes`（忽略大小写与空白）
表示刷新，`""` / `0` / `false` / `no` 一律是"用缓存"。

抓取会写进 `tests/fixtures/network/`；若该目录只读，可用 `MCNDOTAGA_CACHE_DIR`
指向别处（默认不变）：

```bash
MCNDOTAGA_CACHE_DIR=/tmp/mcndotaga-cache .venv/bin/pytest tests/constants -q
```

## 与网络相关的测试

`tests/constants/test_http.py` 用 `tmp_path` + `monkeypatch.setattr(_http, "CACHE", tmp_path)`
覆盖缓存命中/刷新/坏缓存三条语义，**既不读本目录也不打网络**——所以本目录的文件被改坏时，
是常量测试而不是 HTTP 测试报错，且错误信息会点名文件与两种恢复方式。

> 每个用例还必须 `monkeypatch.delenv("MCNDOTAGA_CACHE_DIR", raising=False)`：
> `_cache_dir()` **优先**读该环境变量，而上面偏偏教只读 CI 的用户导出它——
> 少了这一步，测试以为自己在 tmp_path 沙箱里，实际会读写环境变量指向的目录
> （缓存命中那条甚至因此真的打网络）。2026-09-17 修复。
