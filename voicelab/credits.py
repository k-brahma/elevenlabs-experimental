"""クレジット残高の読み取り。

会話は分単位でクレジットを消費するので、**実行の前後で残高を読んで差を残す**。
読み取り自体（`GET /v1/user/subscription`）は課金されない。

会話ごとの正確な費用は、会話の詳細（`GET /v1/convai/conversations/{id}`）の
``metadata.cost`` にある。こちらは会話の実装ができてから使う。
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

API_BASE = 'https://api.elevenlabs.io/v1'
TIMEOUT_SECONDS = 30


class CreditsError(RuntimeError):
    """残高を読めなかった（キー不正、接続失敗、応答の形が違う）。"""


@dataclass(frozen=True)
class CreditsSnapshot:
    """ある時点の残高。

    :ivar tier: プラン名（``free`` / ``starter`` …）。
    :ivar used: 今期に使ったクレジット。
    :ivar limit: 今期の上限。
    :ivar resets_at: 次のリセット日時（UTC）。
    :ivar taken_at: 読んだ日時（UTC）。
    """

    tier: str
    used: int
    limit: int
    resets_at: datetime
    taken_at: datetime

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    def describe(self) -> str:
        """人が読む 1 行。"""
        return (
            f'{self.tier}: {self.used:,} / {self.limit:,} 使用（残り {self.remaining:,}）'
            f' リセット {self.resets_at:%Y-%m-%d %H:%M} UTC'
        )


def read_subscription(api_key: str) -> CreditsSnapshot:
    """残高を 1 回読む。

    :raises CreditsError: 2xx 以外、接続失敗、または必要な項目が無い。
    """
    request = urllib.request.Request(
        f'{API_BASE}/user/subscription',
        headers={'xi-api-key': api_key, 'accept': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise CreditsError(f'ElevenLabs が HTTP {exc.code} を返しました') from exc
    except urllib.error.URLError as exc:
        raise CreditsError('ElevenLabs に接続できませんでした') from exc

    try:
        return CreditsSnapshot(
            tier=str(payload['tier']),
            used=int(payload['character_count']),
            limit=int(payload['character_limit']),
            resets_at=datetime.fromtimestamp(
                int(payload['next_character_count_reset_unix']), tz=timezone.utc
            ),
            taken_at=datetime.now(timezone.utc),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CreditsError('残高の応答に必要な項目がありません') from exc


def consumed(before: CreditsSnapshot, after: CreditsSnapshot) -> int:
    """2 回の読み取りの差（消費クレジット）。期をまたいでリセットされた場合は after の使用量。"""
    if after.resets_at != before.resets_at:
        return after.used
    return max(after.used - before.used, 0)
