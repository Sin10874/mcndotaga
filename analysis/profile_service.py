"""画像请求校验与应用边界。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import re
from urllib.parse import parse_qs

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
import psycopg
import yaml

from contracts.tools.invariants import check_profile
from db.sources import SourceNotAllowed, resolve_sources


class ProfileServiceError(Exception):
    """已分类的业务或数据访问错误，不携带内部连接信息。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ProfileRequest:
    team_id: int
    patch: str
    as_of: date
    sources: tuple[str, ...]


def parse_profile_query(query_string: str, *, today: date | None = None) -> ProfileRequest:
    today = today or datetime.now(timezone.utc).date()
    values = parse_qs(query_string, keep_blank_values=True, max_num_fields=10)
    if set(values) - {"team_id", "patch", "as_of", "sources"}:
        raise ValueError("请求含不支持的参数")
    if any(len(items) != 1 for items in values.values()):
        raise ValueError("请求参数不能重复")
    scalar = {key: items[0] for key, items in values.items()}
    team_text = scalar.get("team_id", "")
    if not re.fullmatch(r"[1-9][0-9]{0,18}", team_text):
        raise ValueError("team_id 必须是正整数")
    team_id = int(team_text)
    if team_id > 2**63 - 1:
        raise ValueError("team_id 超出有效范围")
    patch = scalar.get("patch", "")
    if not re.fullmatch(r"[0-9]{1,2}\.[0-9]{1,3}[a-z]?", patch):
        raise ValueError("patch 必须是明确的版本编号")
    as_of_text = scalar.get("as_of", today.isoformat())
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", as_of_text):
        raise ValueError("as_of 必须采用 YYYY-MM-DD 日期格式")
    try:
        as_of = date.fromisoformat(as_of_text)
    except ValueError:
        raise ValueError("as_of 不是有效日期") from None
    if as_of < date.min + timedelta(days=89):
        raise ValueError("as_of 太早，无法构造完整时间窗")
    if as_of > today:
        raise ValueError("as_of 不能晚于 UTC 今天")
    sources = [item.strip() for item in scalar.get("sources", "pro_match").split(",")]
    if not sources or any(item not in {"pro_match", "pub_match", "scrim"} for item in sources):
        raise ValueError("sources 只接受明确的公开数据来源")
    allowed = resolve_sources(sources)
    return ProfileRequest(team_id, patch, as_of, tuple(allowed))


@lru_cache(maxsize=1)
def _profile_validator():
    document = yaml.safe_load((Path(__file__).parents[1] / "contracts/openapi.yaml").read_text())
    root = Draft202012Validator(document, format_checker=FormatChecker())
    return root.evolve(schema=document["components"]["schemas"]["Profile"])


def validate_profile_body(body, *, request: ProfileRequest):
    try:
        _profile_validator().validate(body)
        if check_profile(body):
            raise ValueError("画像不满足冻结不变式")
        if (body["team_id"], body["patch"], body["as_of"]) != (
            request.team_id, request.patch, request.as_of.isoformat()
        ):
            raise ValueError("响应身份与请求不一致")
        if not set(body["sources_used"]).issubset(request.sources):
            raise ValueError("响应包含未请求的来源")
        for source, coverage in body["coverage"].items():
            if source not in request.sources and any(coverage.values()):
                raise ValueError("未请求来源的覆盖统计必须为零")
    except (ValidationError, ValueError, KeyError, TypeError):
        raise ProfileServiceError("upstream_unavailable", "画像结果未通过数据校验") from None
    return body


def load_profile(dsn: str, request: ProfileRequest):
    """每次请求单独开启可重复读、只读快照。"""
    try:
        with psycopg.connect(dsn.replace("postgresql+psycopg://", "postgresql://"), connect_timeout=5) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '15s'")
            return build_profile_from_connection(conn, request)
    except ProfileServiceError:
        raise
    except psycopg.Error:
        raise ProfileServiceError("upstream_unavailable", "画像数据库暂时不可用") from None


def build_profile_from_connection(conn, request: ProfileRequest):
    from analysis.profile_engine import ProfileInsufficientData, build_profile
    from analysis.profile_repository import ProfileNotFound, load_profile_dataset

    try:
        dataset = load_profile_dataset(
            conn, team_id=request.team_id, patch=request.patch, as_of=request.as_of,
            sources=list(request.sources),
        )
        return validate_profile_body(build_profile(dataset), request=request)
    except ProfileNotFound:
        raise ProfileServiceError("not_found", "请求的战队或版本不存在") from None
    except ProfileInsufficientData:
        raise ProfileServiceError("insufficient_data", "当前窗口的可用样本不足，暂不能形成完整画像") from None
    except SourceNotAllowed:
        raise ProfileServiceError("source_not_allowed", "对手画像不允许使用训练赛来源") from None
    except ProfileServiceError:
        raise
    except (psycopg.Error, ValueError, KeyError, TypeError):
        raise ProfileServiceError("upstream_unavailable", "画像数据或配置暂时不可用") from None
