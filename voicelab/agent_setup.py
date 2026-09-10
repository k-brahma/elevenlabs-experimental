"""A（Agents Platform）側の道具立てを、名前を鍵にして作り直す。

**存在理由**は「何度実行しても同じ状態になること」。計測を仕切り直すたびに Agent を
手で作り直していると、prompt も LLM も声も少しずつずれて、B との比較が成立しなくなる。
そこで、ツール ``search_notes`` と Agent ``voicelab-a`` を**名前で探して、無ければ作り、
あれば上書きする**。設定の正本はこのファイルと ``prompts/agent_system.txt`` に置く。

ここは **REST を直接叩く**（``httpx``）。Agent の作成・更新はクレジットを消費しない。
SDK にも convai の client はあるが、body の形が版によって変わるため、
公式ドキュメントの JSON をそのまま書ける生の REST の方が読んで確かめやすい。
"""

from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import (
    AGENT_PROMPT_PATH,
    DEFAULT_LLM,
    ENV_PATH,
    ConfigError,
    load_env,
    require,
)

API_BASE = 'https://api.elevenlabs.io/v1'
TIMEOUT_SECONDS = 60

#: client tool の名前。Agent 側の登録名と、``ClientTools.register`` の名前は必ず一致させる。
TOOL_NAME = 'search_notes'

#: Agent の名前。id ではなくこの名前を鍵にして探す。
AGENT_NAME = 'voicelab-a'

TOOL_DESCRIPTION = (
    'ノートポータルのノートを検索する。ノートの内容に関する質問には必ずこれを先に呼ぶこと。'
    '結果は関連する節の配列（note = ノート名, heading = 見出し, excerpt = 抜粋）。'
)

#: 会話中にクライアントへ届けてほしいイベント。
#: ``client_tool_call`` が無いと **ツールが一度も呼ばれない**（サーバ側で握り潰される）。
CLIENT_EVENTS = [
    'audio',
    'agent_response',
    'user_transcript',
    'agent_response_correction',
    'client_tool_call',
    'interruption',
]


class SetupError(RuntimeError):
    """Agent かツールの作成・更新に失敗した。"""


@dataclass(frozen=True)
class SetupResult:
    """`setup-agent` の結果。

    :ivar tool_id: client tool の id。
    :ivar tool_created: ツールを新規に作ったか（False なら更新）。
    :ivar agent_id: Agent の id（``agent_...``）。
    :ivar agent_created: Agent を新規に作ったか（False なら更新）。
    :ivar llm: Agent に設定した LLM。
    :ivar env_written: `.env` の `ELEVENLABS_AGENT_ID` を書いたか。
    """

    tool_id: str
    tool_created: bool
    agent_id: str
    agent_created: bool
    llm: str
    env_written: bool

    def describe(self) -> str:
        """人が読む数行。"""
        tool_state = '作成' if self.tool_created else '更新'
        agent_state = '作成' if self.agent_created else '更新'
        return '\n'.join(
            [
                f'ツール {TOOL_NAME}: {tool_state}  id={self.tool_id}',
                f'Agent {AGENT_NAME}: {agent_state}  id={self.agent_id}  llm={self.llm}',
                f'.env の ELEVENLABS_AGENT_ID: {"更新した" if self.env_written else "変更なし"}',
            ]
        )


def _request(client: httpx.Client, method: str, path: str, **kwargs) -> dict:
    """REST を 1 回叩いて JSON を返す。失敗は本文ごと :class:`SetupError` にする。

    ElevenLabs は 422 の本文に「どの項目が違うか」を書いてくるので、握り潰さず見せる。
    """
    try:
        response = client.request(method, f'{API_BASE}{path}', **kwargs)
    except httpx.HTTPError as exc:
        raise SetupError(f'{method} {path} に接続できませんでした: {exc}') from exc
    if response.status_code >= 400:
        raise SetupError(f'{method} {path} が HTTP {response.status_code}: {response.text[:600]}')
    if not response.content:
        return {}
    return response.json()


def _tool_config() -> dict:
    """ツールの定義。``expects_response`` を真にしないと、返した結果が LLM に渡らない。"""
    return {
        'type': 'client',
        'name': TOOL_NAME,
        'description': TOOL_DESCRIPTION,
        'expects_response': True,
        'response_timeout_secs': 20,
        'parameters': {
            'type': 'object',
            'required': ['query'],
            'properties': {
                'query': {
                    'type': 'string',
                    'description': 'ノートを探すための日本語の検索語。利用者の言い回しをそのまま渡してよい。',
                }
            },
        },
    }


def _agent_body(*, prompt: str, llm: str, voice_id: str, model_id: str, tool_id: str) -> dict:
    """Agent の設定。

    ``first_message`` を空にするのは、**こちらが質問を送るまで喋らせない**ため。
    挨拶を先に喋ると、その分の秒数と最初の音までの時間が計測に混ざる。
    """
    return {
        'name': AGENT_NAME,
        'conversation_config': {
            'agent': {
                'first_message': '',
                'language': 'ja',
                'prompt': {
                    'prompt': prompt,
                    'llm': llm,
                    'temperature': 0.3,
                    'max_tokens': 300,
                    'tool_ids': [tool_id],
                },
            },
            'tts': {'voice_id': voice_id, 'model_id': model_id},
            'turn': {'turn_timeout': 30, 'silence_end_call_timeout': 30},
            'conversation': {
                'max_duration_seconds': 120,
                'client_events': CLIENT_EVENTS,
            },
        },
        'platform_settings': {'auth': {'enable_auth': True}},
    }


def _entry_id(item: dict, *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value:
            return str(value)
    return ''


def find_tool(client: httpx.Client, name: str = TOOL_NAME) -> str | None:
    """名前でツールを探して id を返す（無ければ None）。"""
    payload = _request(client, 'GET', '/convai/tools')
    for item in payload.get('tools', []) or []:
        config = item.get('tool_config') or {}
        if config.get('name') == name or item.get('name') == name:
            return _entry_id(item, 'id', 'tool_id')
    return None


def ensure_tool(client: httpx.Client) -> tuple[str, bool]:
    """ツールを作るか更新する。戻り値は ``(tool_id, 作成したか)``。"""
    body = {'tool_config': _tool_config()}
    existing = find_tool(client)
    if existing:
        _request(client, 'PATCH', f'/convai/tools/{existing}', json=body)
        return existing, False
    created = _request(client, 'POST', '/convai/tools', json=body)
    tool_id = _entry_id(created, 'id', 'tool_id')
    if not tool_id:
        raise SetupError(f'ツールの作成応答に id がありません: {created}')
    return tool_id, True


def find_agent(client: httpx.Client, name: str = AGENT_NAME) -> str | None:
    """名前で Agent を探して id を返す（無ければ None）。一覧は cursor で辿る。"""
    params: dict[str, str | int] = {'page_size': 100}
    while True:
        payload = _request(client, 'GET', '/convai/agents', params=params)
        for item in payload.get('agents', []) or []:
            if item.get('name') == name:
                return _entry_id(item, 'agent_id', 'id')
        cursor = payload.get('next_cursor')
        if not payload.get('has_more') or not cursor:
            return None
        params['cursor'] = cursor


def ensure_agent(
    client: httpx.Client,
    *,
    prompt: str,
    llm: str,
    voice_id: str,
    model_id: str,
    tool_id: str,
) -> tuple[str, bool]:
    """Agent を作るか更新する。戻り値は ``(agent_id, 作成したか)``。"""
    body = _agent_body(
        prompt=prompt, llm=llm, voice_id=voice_id, model_id=model_id, tool_id=tool_id
    )
    existing = find_agent(client)
    if existing:
        _request(client, 'PATCH', f'/convai/agents/{existing}', json=body)
        return existing, False
    created = _request(client, 'POST', '/convai/agents/create', json=body)
    agent_id = _entry_id(created, 'agent_id', 'id')
    if not agent_id:
        raise SetupError(f'Agent の作成応答に id がありません: {created}')
    return agent_id, True


def write_agent_id(agent_id: str, path: Path = ENV_PATH) -> bool:
    """`.env` の `ELEVENLABS_AGENT_ID=` の行だけを書き換える（無ければ末尾に足す）。

    行単位で差し替えるのは、**ほかの行（API キーやコメント）に触らない**ため。
    値が既に同じなら書かない。戻り値は書いたかどうか。
    """
    key = 'ELEVENLABS_AGENT_ID'
    lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    for index, line in enumerate(lines):
        if line.strip().startswith(f'{key}='):
            if line.strip() == f'{key}={agent_id}':
                return False
            lines[index] = f'{key}={agent_id}'
            break
    else:
        lines.append(f'{key}={agent_id}')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return True


def setup(env: dict[str, str] | None = None, *, env_path: Path = ENV_PATH) -> SetupResult:
    """ツールと Agent を、この repo の設定どおりの状態にする。

    :raises ConfigError: `.env` に API キーや声の id が無い。
    :raises SetupError: ElevenLabs 側が受け付けなかった。
    """
    env = load_env() if env is None else env
    api_key = require('ELEVENLABS_API_KEY', env)
    voice_id = require('ELEVENLABS_VOICE_ID', env)
    model_id = require('ELEVENLABS_MODEL_ID', env)
    llm = env.get('VOICELAB_LLM') or DEFAULT_LLM
    if not AGENT_PROMPT_PATH.exists():
        raise ConfigError(f'Agent の指示が見つかりません: {AGENT_PROMPT_PATH.name}')
    prompt = AGENT_PROMPT_PATH.read_text(encoding='utf-8').strip()

    headers = {'xi-api-key': api_key, 'accept': 'application/json'}
    with httpx.Client(headers=headers, timeout=TIMEOUT_SECONDS) as client:
        tool_id, tool_created = ensure_tool(client)
        agent_id, agent_created = ensure_agent(
            client,
            prompt=prompt,
            llm=llm,
            voice_id=voice_id,
            model_id=model_id,
            tool_id=tool_id,
        )
    return SetupResult(
        tool_id=tool_id,
        tool_created=tool_created,
        agent_id=agent_id,
        agent_created=agent_created,
        llm=llm,
        env_written=write_agent_id(agent_id, env_path),
    )
