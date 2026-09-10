"""`.env` の読み込み。

外部ライブラリを増やさないため、`KEY=VALUE` だけを見る素朴な実装にしている。
骨組み（credits / report）を標準ライブラリだけで動かせる状態を保つのが狙い。
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / '.env'

#: 結果を書き出す先。CSV と Markdown の表を置く。
RESULTS_DIR = ROOT / 'results'

#: 題材データ（架空のノート）。実際の vault は使わない。
CORPUS_DIR = ROOT / 'corpus'

#: 質問集。
SCENARIOS_PATH = ROOT / 'scenarios' / 'questions.json'

#: Agent に持たせる指示。コードから切り離してあるのは、文言を直したときに
#: コードを触らず `setup-agent` の再実行だけで反映させたいため。
AGENT_PROMPT_PATH = ROOT / 'prompts' / 'agent_system.txt'

#: 会話の録音。あとで聞いて正誤を判定するために残す。
AUDIO_DIR = RESULTS_DIR / 'audio'

#: 返答の本文と時刻。音を聞かなくても何が起きたか追えるようにする。
TRANSCRIPTS_DIR = RESULTS_DIR / 'transcripts'

#: 環境変数からの上書きを許すキー。`.env` に書かれていなくても拾う。
ENV_KEYS = (
    'ELEVENLABS_API_KEY',
    'ELEVENLABS_AGENT_ID',
    'ELEVENLABS_VOICE_ID',
    'ELEVENLABS_MODEL_ID',
    'VOICELAB_LLM',
)

#: Agent の応答生成に使う LLM の既定。`.env` の `VOICELAB_LLM` で変えられる。
DEFAULT_LLM = 'gemini-2.5-flash'


class ConfigError(RuntimeError):
    """設定が足りない。"""


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """`.env` を読んで辞書で返す（無ければ空）。

    既に環境変数にある値を優先する。CI や一時的な上書きから使えるようにするため。
    """
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in list(values) + list(ENV_KEYS):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict]:
    """質問集を読む。A と B が**同じ質問を同じ順で**流すための唯一の入口。"""
    return json.loads(path.read_text(encoding='utf-8'))['questions']


def find_scenario(scenario_id: str, path: Path = SCENARIOS_PATH) -> dict:
    """id で 1 問だけ取る。

    :raises ConfigError: その id が質問集に無い。
    """
    for item in load_scenarios(path):
        if item['id'] == scenario_id:
            return item
    known = ', '.join(item['id'] for item in load_scenarios(path))
    raise ConfigError(f'{scenario_id!r} という質問はありません。あるのは: {known}')


def require(key: str, env: dict[str, str] | None = None) -> str:
    """必須の設定を取る。無ければ何を書けばよいかを言って止まる。

    :raises ConfigError: 値が空、または未設定。
    """
    env = load_env() if env is None else env
    value = env.get(key, '')
    if not value:
        raise ConfigError(f'{key} が未設定です。.env に書いてください（.env.example を参照）。')
    return value
