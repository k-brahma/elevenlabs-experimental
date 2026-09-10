"""`.env` の読み込み。

外部ライブラリを増やさないため、`KEY=VALUE` だけを見る素朴な実装にしている。
骨組み（credits / report）を標準ライブラリだけで動かせる状態を保つのが狙い。
"""

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
    for key in list(values) + ['ELEVENLABS_API_KEY', 'ELEVENLABS_AGENT_ID']:
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def require(key: str, env: dict[str, str] | None = None) -> str:
    """必須の設定を取る。無ければ何を書けばよいかを言って止まる。

    :raises ConfigError: 値が空、または未設定。
    """
    env = load_env() if env is None else env
    value = env.get(key, '')
    if not value:
        raise ConfigError(f'{key} が未設定です。.env に書いてください（.env.example を参照）。')
    return value
