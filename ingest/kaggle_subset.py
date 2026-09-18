"""只下需要的三个 CSV，共 506.2 MB。**绝不整包下载**。

数据集总计 48.52 GB（1546 个文件）。其中 players.csv 是 **19 个分片文件**
合计 41.04 GB（最大单片 2025/players.csv = 6.92 GB）——不存在"单文件 41 GB"，
但无论如何都与 Phase A 无关。

白名单（相对数据集根，规格 §10.2）：

| 文件 | 大小 | 目录数 |
|---|---|---|
| `*/picks_bans.csv` | 198.8 MB | 19 |
| `*/main_metadata.csv` | 66.7 MB | 19 |
| `*/draft_timings.csv` | 240.7 MB | **10**（仅 2016–2025；已知缺口，以 picks_bans 为权威） |

落到 `tests/fixtures/kaggle/<folder>/<file>`（该目录已由 `.gitignore` 排除，**不入库**）。
下载走 `kaggle` 包的**单文件**接口 `dataset_download_file`（对应
`/api/v1/datasets/download/{owner}/{dataset}/{fileName}`），不是整包 `dataset_download_files`。

**对已安装客户端（kaggle 2.2.4）源码的三条实测结论**（本机 `pip install -e ".[dev,ingest]"`
成功，故这部分不是猜的）：

1. `dataset_download_file(dataset, file_name, path=None, force=False, quiet=True)` 存在，
   签名与本模块的调用一致；
2. `dataset_list_files(dataset, page_token=None, page_size=20)` **默认 20 条/页**，而本数据集
   有 1546 个文件 —— 必须显式给 `page_size` 并翻页，否则白名单文件大概率一个都看不到。
   见 `list_all_files`；
3. 落盘名取的是**下载 URL 的最后一段**（源码
   `outfile = os.path.join(effective_path, url.split("?")[0].split("/")[-1])`），不是
   `file_name`。URL 形状无法在无凭证环境下观察，故 `download_one` 在目标路径不存在时做一次
   **有界**补救（只认"本次调用新出现的唯一文件"），否则报错。**仍未验证**的是这一段的真实
   落盘名（需要凭证才能看到 URL）。

**凭证（本数据集其实不需要）**：`bwandowando/dota-2-pro-league-matches-2023` 是 **CC0 公共数据集**，
它的清单与下载端点**对匿名请求同样返回数据**（2026-09-18 实测：清单 200、下载 302 → 200，
见 `AnonymousKaggleApi` 的 docstring）。故本模块**不再把凭证当前置条件**：

- **有**凭证（环境变量 `KAGGLE_USERNAME`/`KAGGLE_KEY`、`KAGGLE_API_TOKEN`，或 `~/.kaggle/kaggle.json`
  / `~/.kaggle/access_token`）→ 走 `kaggle` 官方客户端（`[ingest]` extra），**这是默认路径**；
- **没有**凭证 → 走 `AnonymousKaggleApi`（纯 httpx，核心依赖即可），CLI 照常工作。
  只有匿名端点被上游拒绝（401/403）时才会要求凭证（`KaggleCredentialsMissing` + 补齐方式）。

修订原因（评审 F8）：上一版的 CLI 硬性要求凭证（`authenticate()` 失败即 `SystemExit(1)`），
于是文档里给出的复现命令在一台没有凭证的机器上**根本跑不出这份缓存** —— 与实际能跑通的
`KAGGLE_USERNAME=bogus KAGGLE_KEY=bogus` 相矛盾。CC0 数据集不该有这道门。

安装：`python -m pip install -e ".[dev,ingest]"`（`kaggle` / `python-dotenv` 在 `[ingest]` extra 里）。
匿名路径只用到核心依赖 httpx，故**没装 extra 也能下载**；`kaggle` 只在有凭证时才 import。

失败行为：缺依赖且**有**凭证 → 退回匿名路径并警告；匿名端点 401/403 →
`KaggleCredentialsMissing`（含补齐方式与下载命令）。CLI 把可操作的错误变成**非零退出 +
一行提示，不打印 traceback**。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import urllib.parse
from typing import Iterable

import httpx

REPO = pathlib.Path(__file__).parents[1]
DEFAULT_CACHE_DIR = REPO / "tests" / "fixtures" / "kaggle"

DATASET = "bwandowando/dota-2-pro-league-matches-2023"

#: Kaggle 的匿名可用端点（2026-09-18 实测，无需任何凭证）：
#: - 清单：`GET /api/v1/datasets/list/{owner}/{dataset}?pageSize=&pageToken=` → 200 JSON
#:   （`datasetFiles[]` 带 `name`/`totalBytes`，翻页用 `nextPageToken`）；
#: - 下载：`GET /api/v1/datasets/download/{owner}/{dataset}/{file_name}` → 302 → 200。
#:   **`file_name` 必须整体百分号编码**（`Constants/Constants.Leagues.csv` 要写成
#:   `Constants%2FConstants.Leagues.csv`）：不编码时路径里的 `/` 会被当成额外路径段，
#:   服务器直接 404（实测）。
KAGGLE_BASE_URL = "https://www.kaggle.com"
LIST_FILES_PATH = "/api/v1/datasets/list/{slug}"
DOWNLOAD_FILE_PATH = "/api/v1/datasets/download/{slug}/{file_name}"

#: 相对数据集根的文件白名单（按 **basename** 匹配，见 `select_files`）。
WHITELIST = ("main_metadata.csv", "picks_bans.csv", "draft_timings.csv")

#: 规格 §10.2 的清单**之外**、但实测结构上必需的文件（精确相对路径）。
#:
#: 实测（2026-09-18，本机跑通真实下载后逐列检查）：`main_metadata.csv` **没有**
#: `league_name` 列（2016/2018/2025 三个样本年度的列集都没有，列名是
#: `leagueid`/`league_name` 里的后者根本不存在），而 `leagues.name` 是 `NOT NULL`、
#: M1 又要求 `leagues` 可查（> 50 行）。联赛名只在这一个文件里：
#: `Constants/Constants.Leagues.csv`（429 KB，列 = `leagueid,leaguename,tier`）。
#: 不加它 → `leagues` 一行都写不进去（或必须编造名字），Task 13 的 M1 断言直接不可能通过。
#: 代价：506.2 MB → 506.6 MB。
EXTRA_FILES = ("Constants/Constants.Leagues.csv",)

#: 规格 §10.2 的目录数。只用于**报警**（上游会更新数据集），不作断言。
EXPECTED_MIN_FOLDERS = {"main_metadata.csv": 19, "picks_bans.csv": 19, "draft_timings.csv": 10}

INSTALL_COMMAND = 'python -m pip install -e ".[dev,ingest]"'
DOWNLOAD_COMMAND = "python -m ingest.kaggle_subset"

CREDENTIALS_HELP = (
    "匿名端点拒绝了这次请求（401/403）：上游可能已经收紧了对本数据集的匿名访问。\n"
    "  本数据集是 CC0 公共数据集，正常情况下**不需要凭证**（直接运行 "
    f"{DOWNLOAD_COMMAND} 即可）。\n"
    "  若确实需要凭证，补齐方式（二选一）：\n"
    "  1) 环境变量：export KAGGLE_USERNAME=... KAGGLE_KEY=...\n"
    "  2) ~/.kaggle/kaggle.json：kaggle.com → Account → Create New API Token，"
    "下载后放到该路径（chmod 600）\n"
    "  也可以写进仓库根的 .env：cp .env.example .env 后填 KAGGLE_USERNAME/KAGGLE_KEY"
    "（.env 已在 .gitignore 中，不会入库）\n"
    "  （kaggle 2.x 另支持 token 流：export KAGGLE_API_TOKEN=... 或 ~/.kaggle/access_token）\n"
    f"凭证就位后运行：{DOWNLOAD_COMMAND}（有凭证时自动走已认证路径）"
)


class KaggleCredentialsMissing(RuntimeError):
    """匿名端点要求凭证（401/403）时才抛 —— 提示里必须写清怎么补（规格 §10.2 要求凭证只存本地）。"""


class KaggleDependencyMissing(RuntimeError):
    """没有装 `[ingest]` extra（只影响"有凭证时走官方客户端"这条路径）。"""


# ------------------------------------------------------------------------ 凭证

def kaggle_json_path() -> pathlib.Path:
    """`~/.kaggle/kaggle.json`，可用 `KAGGLE_CONFIG_DIR` 覆盖（kaggle 官方客户端同样认它）。"""
    return _config_dir() / "kaggle.json"


def access_token_path() -> pathlib.Path:
    """kaggle 2.x 的 `~/.kaggle/access_token`（OAuth 流落盘的位置）。"""
    return _config_dir() / "access_token"


def _config_dir() -> pathlib.Path:
    override = os.environ.get("KAGGLE_CONFIG_DIR")
    return pathlib.Path(override) if override else pathlib.Path.home() / ".kaggle"


def credentials_present() -> bool:
    """只做环境/文件检查，**不 import kaggle**（缺依赖时要能给出"缺凭证"而不是 ImportError）。

    两条口径都认（实测 kaggle 2.2.4 的 `authenticate()` 按
    1) access token → 2) legacy key → 3) OAuth 的顺序尝试）：

    - **legacy**（计划 Step 3 的写法）：`KAGGLE_USERNAME` + `KAGGLE_KEY`，或 `~/.kaggle/kaggle.json`；
    - **2.x token 流**：`KAGGLE_API_TOKEN`，或 `~/.kaggle/access_token`。
      只认 legacy 会让"已经用 `kaggle auth login` 登过"的机器被误报成缺凭证。
    """
    if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"):
        return True
    if os.environ.get("KAGGLE_API_TOKEN"):
        return True
    return kaggle_json_path().is_file() or access_token_path().is_file()


def require_credentials() -> None:
    """匿名端点被拒（401/403）时才会用到：**没有**凭证就抛出可操作的补齐提示。

    它**不再**是 `download()` 的前置条件 —— 本数据集是 CC0，匿名端点正常返回数据
    （见模块 docstring 的「凭证」一节）。
    """
    if not credentials_present():
        raise KaggleCredentialsMissing(CREDENTIALS_HELP)


def load_env_file(path: pathlib.Path | None = None, *, log=print) -> bool:
    """读 `.env`（若存在）。返回是否真的加载了。

    `python-dotenv` 缺失**不是**错误：凭证也可以来自环境变量或 `~/.kaggle/kaggle.json`，
    故这里只警告并继续。默认路径是仓库根的 `.env`（不是 cwd）—— 计划 Task 12 Step 3 要求
    `cp .env.example .env` 后从 `.env` 读，而 `.env` 就该在仓库根。
    """
    path = pathlib.Path(path) if path is not None else REPO / ".env"
    if not path.is_file():
        return False
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        log(f"警告：{path} 存在但 python-dotenv 未安装（{INSTALL_COMMAND}），本次忽略该文件")
        return False
    load_dotenv(path)
    return True


# ------------------------------------------------------------------------ 白名单

def select_files(names: Iterable[str]) -> list[str]:
    """按 basename（外加 `EXTRA_FILES` 的精确路径）过滤数据集清单，保持原顺序、去重。

    白名单是"选择性下载 506 MB"的**唯一实现处**：一旦这里放宽，`players.csv` 的 41 GB
    就会被拉下来。故它单独成函数并被测试直接覆盖。
    """
    selected: list[str] = []
    for name in names:
        keep = name.rsplit("/", 1)[-1] in WHITELIST or name in EXTRA_FILES
        if keep and name not in selected:
            selected.append(name)
    return selected


def manifest_entries(response) -> list[tuple[str, int | None]]:
    """把 `dataset_list_files` 的单页响应适配成 `[(文件名, 字节数)]`。

    kaggle 客户端跨版本返回过对象 / 字典 / 字符串三种形状，字段名有 `name` / `ref`、
    `total_bytes` / `size` 多种；这里全部接住 —— 认不出形状只会让白名单为空，而那是**报错**，
    不是静默少下。
    """
    entries = getattr(response, "files", response)
    out: list[tuple[str, int | None]] = []
    for entry in entries or []:
        if isinstance(entry, str):
            name, size = entry, None
        elif isinstance(entry, dict):
            name = entry.get("name") or entry.get("ref")
            size = entry.get("total_bytes") or entry.get("totalBytes") or entry.get("size")
        else:
            name = getattr(entry, "name", None) or getattr(entry, "ref", None)
            size = getattr(entry, "total_bytes", None) or getattr(entry, "size", None)
        if name:
            out.append((str(name), int(size) if size else None))
    return out


def manifest_names(response) -> list[str]:
    """单页响应里的文件名（`manifest_entries` 的薄封装）。"""
    return [name for name, _size in manifest_entries(response)]


#: 单页条数。**必须显式给大值**：kaggle 2.2.4 的 `dataset_list_files(page_size=20)` 默认
#: 只返回 20 条，而本数据集有 1546 个文件 —— 用默认值只会看到第一页，白名单里的文件大概率
#: 一个都不在，然后以"清单里没有白名单文件"报错（或更糟：只下到前 20 个里的那几个）。
LIST_PAGE_SIZE = 1000
#: 翻页上限：纯粹防"上游永远返回同一个 next_page_token"把 CLI 挂死。
MAX_LIST_PAGES = 200


def list_all_files(client, dataset: str, *, page_size: int = LIST_PAGE_SIZE,
                   max_pages: int = MAX_LIST_PAGES, log=print) -> dict[str, int | None]:
    """翻完所有页的文件清单 → `{文件名: 字节数}`（`dataset_list_files` 默认只有 20 条/页）。"""
    files: dict[str, int | None] = {}
    token: str | None = None
    for page in range(1, max_pages + 1):
        response = client.dataset_list_files(dataset, page_token=token, page_size=page_size)
        error = getattr(response, "error_message", None)
        if error:
            raise RuntimeError(f"{dataset} 的清单接口返回错误：{error}")
        files.update(dict(manifest_entries(response)))
        token = getattr(response, "next_page_token", None) or None
        if not token:
            if page > 1:
                log(f"清单共 {page} 页 / {len(files)} 个文件")
            return files
    raise RuntimeError(
        f"{dataset} 的清单翻页超过 {max_pages} 页仍未结束（已收 {len(files)} 个文件）："
        f"上游分页行为异常，拒绝用一个可能不完整的清单去决定下哪些文件。")


#: 缓存盘点时认识的 basename（年度目录里的三个 CSV + `EXTRA_FILES` 的 basename）。
KNOWN_BASENAMES = (*WHITELIST, *(name.rsplit("/", 1)[-1] for name in EXTRA_FILES))


def summarize(cache_dir) -> dict:
    """盘点缓存：每个已知文件有几个、分布在哪些目录、共多少字节。"""
    cache_dir = pathlib.Path(cache_dir)
    files = {name: 0 for name in KNOWN_BASENAMES}
    folders: dict[str, list[str]] = {}
    total_bytes = 0
    for path in sorted(cache_dir.glob("*/*.csv")) if cache_dir.is_dir() else []:
        if path.name not in files:
            continue
        files[path.name] += 1
        folders.setdefault(path.parent.name, []).append(path.name)
        total_bytes += path.stat().st_size
    return {"files": files, "folders": {k: sorted(v) for k, v in sorted(folders.items())},
            "n_folders": len(folders), "total_bytes": total_bytes}


# ------------------------------------------------------------------------ 下载

def _build_api():
    """有凭证时走 `kaggle` 官方客户端（自己读 `KAGGLE_*` / `~/.kaggle/kaggle.json`）。"""
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
    except ModuleNotFoundError as exc:
        raise KaggleDependencyMissing(
            f"缺少 kaggle 依赖（{exc}）。安装：{INSTALL_COMMAND}") from exc
    api = KaggleApi()
    api.authenticate()
    return api


class _FilePage:
    """`dataset_list_files` 的响应形状（与 kaggle 客户端的 proto 响应同名同义）。"""

    __slots__ = ("files", "next_page_token", "error_message")

    def __init__(self, files, next_page_token=None, error_message=None):
        self.files = files
        self.next_page_token = next_page_token
        self.error_message = error_message


class AnonymousKaggleApi:
    """**无凭证**的 Kaggle 数据接口客户端（只实现 `download()` 用到的两个方法）。

    为什么可以没有凭证：`bwandowando/dota-2-pro-league-matches-2023` 是 **CC0 公共数据集**，
    2026-09-18 本机实测（无 `KAGGLE_USERNAME`/`KAGGLE_KEY`、无 `~/.kaggle/`）：

    - 清单 `GET /api/v1/datasets/list/{slug}?pageSize=3` → **200** JSON（`datasetFiles[]` / `nextPageToken`）；
    - 下载 `GET /api/v1/datasets/download/{slug}/{quote(file_name)}` → **302 → 200**（4 KB–66 MB 都通）。

    形状刻意对齐 `kaggle.api.kaggle_api_extended.KaggleApi`（同名方法、同名返回字段），
    故 `list_all_files` / `download_one` 对两种客户端是同一段代码。这是**实测事实，不是契约**：
    上游随时可能要求有效凭证，那时 401/403 会转成 `KaggleCredentialsMissing` + 补齐方式。

    `client` 可注入（测试用 `httpx.MockTransport` 构造假客户端，不碰网络）。
    """

    def __init__(self, *, base_url: str = KAGGLE_BASE_URL, client: httpx.Client | None = None,
                 log=print):
        self.base_url = base_url.rstrip("/")
        self.log = log
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=30.0),
            follow_redirects=True, headers={"User-Agent": "mcndotaga-ingest/0.1 (CC0 dataset)"})

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, *, params=None) -> httpx.Response:
        url = f"{self.base_url}{path}"
        response = self._client.get(url, params=params)
        if response.status_code in (401, 403):
            # 匿名被拒 = 现在真的需要凭证了：给出与 `require_credentials()` 同一份提示
            raise KaggleCredentialsMissing(
                f"{url} 返回 {response.status_code}。\n{CREDENTIALS_HELP}")
        if response.status_code >= 400:
            raise RuntimeError(
                f"{url} 返回 {response.status_code}：{response.text[:200]!r}")
        return response

    def dataset_list_files(self, dataset: str, page_token=None, page_size=20) -> _FilePage:
        params: dict[str, object] = {"pageSize": int(page_size)}
        if page_token:
            params["pageToken"] = page_token
        payload = self._get(LIST_FILES_PATH.format(slug=dataset), params=params).json()
        files = [{"name": entry.get("name") or entry.get("ref"),
                  "total_bytes": entry.get("totalBytes") or entry.get("size")}
                 for entry in payload.get("datasetFiles") or []]
        return _FilePage(files=files,
                         next_page_token=payload.get("nextPageToken") or None,
                         error_message=payload.get("errorMessage") or None)

    def dataset_download_file(self, dataset: str, file_name: str, path=None, force=False,
                              quiet=True) -> None:
        """下载单个文件到 `path/<basename>`（与官方客户端同语义：落盘名 = 文件名本身）。"""
        dest_dir = pathlib.Path(path or ".")
        dest_dir.mkdir(parents=True, exist_ok=True)
        # 整体编码：文件名里的 `/` **必须**变成 `%2F`，否则服务器把目录当成额外路径段 → 404。
        encoded = urllib.parse.quote(file_name, safe="")
        url = f"{self.base_url}{DOWNLOAD_FILE_PATH.format(slug=dataset, file_name=encoded)}"
        target = dest_dir / pathlib.Path(file_name).name
        with self._client.stream("GET", url) as response:
            if response.status_code in (401, 403):
                raise KaggleCredentialsMissing(
                    f"{url} 返回 {response.status_code}。\n{CREDENTIALS_HELP}")
            if response.status_code >= 400:
                raise RuntimeError(f"{url} 返回 {response.status_code}")
            with open(target, "wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    handle.write(chunk)


def build_client(*, api=None, log=print):
    """按凭证选择客户端：**有凭证走官方客户端（默认）**，没有则走匿名端点。

    `kaggle` 依赖缺失但**有**凭证时退回匿名路径并警告 —— 对 CC0 数据集，缺一个可选依赖
    不该让"照着文档跑"失败。
    """
    if api is not None:
        return api
    if credentials_present():
        try:
            return _build_api()
        except KaggleDependencyMissing as exc:
            log(f"警告：{exc}；改走匿名 HTTP 端点（本数据集是 CC0，匿名端点同样返回数据）")
    else:
        log("未检测到 Kaggle 凭证：走匿名 HTTP 端点（本数据集是 CC0 公共数据集，"
            "清单/下载端点对匿名请求同样返回数据；有凭证时自动走已认证路径）")
    return AnonymousKaggleApi(log=log)


def download_one(client, dataset: str, name: str, cache_dir: pathlib.Path, *,
                 expected_bytes: int | None = None, log=print) -> tuple[pathlib.Path, bool]:
    """下一个文件并保证它落在 `cache_dir/<name>`；返回 `(路径, 是否真的下载了)`。

    **可续跑**：本地文件大小与清单一致时直接跳过。506 MB 的下载中断后重跑不该从头再来，
    而 `force=True` 每次都会重下全部 —— 这也是"每 N 场 commit 一次以便中断可续"的同一条理由。

    **为什么不能只信 `path` 参数**：kaggle 2.2.4 的 `dataset_download_file` 把落盘名写成
    下载 URL 的最后一段（源码：`outfile = os.path.join(effective_path,
    url.split("?")[0].split("/")[-1])`），而不是 `file_name`。实测本数据集下两者同名
    （落盘名 == basename），但 URL 形状随版本/接口变化，所以这里做一次**有界**补救：
    只认"这次调用新出现的、且全目录唯一的新文件"，唯一才改名；否则**报错并列出目录内容**，
    绝不猜。
    """
    dest = cache_dir / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if expected_bytes and dest.is_file() and dest.stat().st_size == expected_bytes:
        log(f"  跳过 {name}（本地 {expected_bytes} 字节与清单一致）")
        return dest, False
    before = {p.name for p in dest.parent.iterdir()}
    client.dataset_download_file(dataset, name, path=str(dest.parent), force=True, quiet=True)
    if dest.is_file():
        return dest, True
    new_files = sorted(p for p in dest.parent.iterdir() if p.name not in before and p.is_file())
    if len(new_files) == 1:
        log(f"  {name}: kaggle 客户端落盘为 {new_files[0].name}，按白名单路径改名为 {dest.name}")
        os.replace(new_files[0], dest)
        return dest, True
    raise RuntimeError(
        f"下载 {name} 后既没有 {dest}，也没有「唯一的新文件」可认"
        f"（新文件 = {[p.name for p in new_files]}，目录现有 = "
        f"{sorted(p.name for p in dest.parent.iterdir())}）：拒绝猜测哪个是目标文件，"
        f"请人工确认 kaggle 客户端的落盘命名规则。")


def download(cache_dir=DEFAULT_CACHE_DIR, *, dataset: str = DATASET, api=None, log=print,
             list_only: bool = False) -> dict:
    """按白名单下载到 `cache_dir/<folder>/<file>`，返回 `summarize()` 的盘点。

    **没有凭证也能跑**：`api` 未注入时由 `build_client()` 选客户端（有凭证 → 官方客户端；
    没有 → `AnonymousKaggleApi`）。注入 `api` 的测试路径同样不再需要凭证 —— 凭证不是这条
    路径的前置条件（`require_credentials()` 只在匿名端点 401/403 时由客户端抛出）。

    `list_only=True` 只列清单并报出白名单命中与总字节数，**不下载任何文件** ——
    用于"先看清要下什么"与无凭证/有凭证两条路径的冒烟核对。
    """
    load_env_file(log=log)
    cache_dir = pathlib.Path(cache_dir)
    client = build_client(api=api, log=log)
    try:
        manifest = list_all_files(client, dataset, log=log)
        wanted = select_files(manifest)
        if not wanted:
            raise RuntimeError(
                f"{dataset} 的文件清单里没有任何白名单文件（清单 {len(manifest)} 项，"
                f"白名单 {WHITELIST} + {EXTRA_FILES}）：清单前几项 = {list(manifest)[:5]}。"
                f"检查数据集 slug、kaggle 客户端接口形状或分页。")

        expected_bytes = sum(manifest[n] for n in wanted if manifest[n])
        log(f"白名单命中 {len(wanted)} 个文件 / 清单合计 {expected_bytes / 1e6:.1f} MB")
        if list_only:
            summary = summarize(cache_dir)
            summary.update({"manifest_files": len(manifest), "wanted": wanted,
                            "requested_files": len(wanted), "downloaded_files": 0,
                            "expected_bytes": expected_bytes, "list_only": True})
            log(f"--list-only：未下载任何文件（缓存现有 {summary['total_bytes'] / 1e6:.1f} MB，"
                f"{summary['n_folders']} 个目录）")
            return summary

        downloaded = 0
        for name in wanted:
            _path, fetched = download_one(client, dataset, name, cache_dir,
                                          expected_bytes=manifest.get(name), log=log)
            downloaded += fetched
    finally:
        # 只关自己建的客户端：注入的客户端归调用方管（测试里还要继续用）
        if api is None and hasattr(client, "close"):
            client.close()

    summary = summarize(cache_dir)
    summary["requested_files"] = len(wanted)
    summary["downloaded_files"] = downloaded
    log(f"下载完成：{summary['files']}（缓存共 {summary['total_bytes'] / 1e6:.1f} MB，"
        f"{summary['n_folders']} 个目录；本次实际下载 {downloaded} 个文件）")
    short = {name: (count, EXPECTED_MIN_FOLDERS[name])
             for name, count in summary["files"].items()
             if name in EXPECTED_MIN_FOLDERS and count < EXPECTED_MIN_FOLDERS[name]}
    if short:
        log(f"警告：以下文件的目录数少于规格 §10.2 的预期（{short}）——"
            f"draft_timings 本来就只有 10/19 个目录，其余缺口需要人看一眼")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ingest.kaggle_subset",
        description="按白名单下载 Kaggle 引导数据集的 506.7 MB 子集（绝不整包下载）。"
                    "该数据集是 CC0 公共数据集：**无需 Kaggle 凭证**（匿名端点实测可用），"
                    "有凭证时自动走已认证路径")
    parser.add_argument("--cache-dir", type=pathlib.Path, default=DEFAULT_CACHE_DIR,
                        help=f"落盘目录（默认 {DEFAULT_CACHE_DIR}）")
    parser.add_argument("--dataset", default=DATASET, help=f"数据集 slug（默认 {DATASET}）")
    parser.add_argument("--list-only", action="store_true",
                        help="只拉文件清单并报出白名单命中，不下载任何文件（自检/预览用）")
    args = parser.parse_args(argv)

    try:
        summary = download(args.cache_dir, dataset=args.dataset, list_only=args.list_only)
    except (KaggleCredentialsMissing, KaggleDependencyMissing) as exc:
        print(f"\n[ingest.kaggle_subset] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:                      # 上游/网络/磁盘：给一行提示，不吐 traceback
        print(f"\n[ingest.kaggle_subset] 下载失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if args.list_only:
        print(f"[ingest.kaggle_subset] 清单 {summary['manifest_files']} 个文件；"
              f"白名单命中 {summary['requested_files']} 个 / "
              f"{summary['expected_bytes'] / 1e6:.1f} MB（未下载）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
