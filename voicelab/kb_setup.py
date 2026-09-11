"""C（Knowledge Base）側の道具立てを、名前を鍵にして作り直す。

**存在理由**は A・B との違いを 1 か所に閉じ込めること。A と B は**ノートの本文を手元に
置いたまま**で、引くのは自前の :mod:`voicelab.search` だった。C は逆に、``corpus/`` の本文を
**ElevenLabs 側にアップロードして預ける**。Agent は道具を 1 つも持たず、ElevenLabs 内部の
RAG がノートを引いて LLM に渡す。ツール往復（A の遅延の 1.5〜2 秒ぶん）が消える代わりに、
ノートが外に出て、ノートを直すたびに同期が要る ―― その長短を数字で示すための構成。

やることは 3 つで、どれも**名前を鍵にして冪等**にしてある（:mod:`voicelab.agent_setup` と同じ作法）。

1. ``corpus/*.md`` の本文を Knowledge Base に登録する（文書名はファイル名から拡張子を落としたもの）
2. その文書に RAG の索引を張り、出来上がるまで待つ
3. Agent ``voicelab-c`` を作る／更新して、文書を紐付ける（**道具は持たせない**）

声・TTS モデル・LLM・``turn``・``client_events``・``max_duration_seconds``・
``text_normalisation_type``・``enable_auth`` は A と同じ値にする。そのために設定の組み立ては
:func:`voicelab.agent_setup.build_agent_body` を**共有**していて、ここで足すのは
``knowledge_base`` と ``rag`` だけ。設定がずれると、測った差が構成の差だと言えなくなる。

索引の埋め込みモデルは **``multilingual_e5_large_instruct`` で固定**する。既定の
``e5_mistral_7b_instruct`` は英語寄りで、日本語のノートでは引けないため。
"""

import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .agent_setup import (
    API_BASE,
    TIMEOUT_SECONDS,
    SetupError,
    build_agent_body,
    find_agent,
    request_json,
    write_agent_id,
)
from .config import (
    CORPUS_DIR,
    DEFAULT_LLM,
    ENV_PATH,
    KB_PROMPT_PATH,
    ConfigError,
    load_env,
    require,
)

#: C の Agent の名前。id ではなくこの名前を鍵にして探す。
KB_AGENT_NAME = 'voicelab-c'

#: `.env` に書く鍵。A の ``ELEVENLABS_AGENT_ID`` とは別にする（両方を残して測り比べるため）。
KB_AGENT_ID_KEY = 'ELEVENLABS_KB_AGENT_ID'

#: 埋め込みモデル。**日本語なので multilingual を使う。** 既定の
#: ``e5_mistral_7b_instruct`` は英語寄りで、この題材では引けない。
RAG_MODEL = 'multilingual_e5_large_instruct'

#: 1 回の応答で LLM に渡す最大チャンク数。A の ``search_notes`` の既定（3 件）と揃える。
#: 渡す材料の量が違うと、遅延も費用も比べられなくなるため。
RAG_MAX_CHUNKS = 3

#: 索引の出来上がりを待つ間隔と上限（秒）。
RAG_POLL_SECONDS = 2.0
RAG_TIMEOUT_SECONDS = 120.0

#: 索引の状態。2026-09-11 に実測した値は ``created`` → ``processing`` → ``succeeded`` で、
#: ``progress_percentage`` が 100 になると ``succeeded`` になる。
#:
#: **``created`` は「索引の枠を作った」だけで、まだ引けない。** 出来上がりと取り違えると、
#: 索引が空のまま会話を流して「ノートには見当たりません」しか返ってこない。だから
#: **出来上がりは ``succeeded`` だけ**にして、知らない綴りは「まだ途中」とみなして待つ
#: （待ちすぎても :data:`RAG_TIMEOUT_SECONDS` で止まる。勝手に成功とみなすより安全）。
RAG_DONE_STATUSES = frozenset({'succeeded', 'success'})
RAG_FAILED_STATUSES = frozenset({'failed', 'failure', 'error', 'canceled', 'cancelled'})


class KbSetupError(SetupError):
    """Knowledge Base か C の Agent を作れなかった。

    :class:`voicelab.agent_setup.SetupError` を継いでいるのは、CLI の受け口を
    増やさずに同じ扱い（「エラー: …」を出して 1 を返す）にするため。
    """


@dataclass(frozen=True)
class KbDocument:
    """Knowledge Base に預けた 1 本のノート。

    :ivar name: 文書名。``search.py`` が使うノート名（ファイル名から拡張子を落としたもの）と
        揃える。返答の「デプロイ手順によると」が A と同じ言い方になるようにするため。
    :ivar id: ElevenLabs 側の文書 id。
    :ivar created: 新しく登録したか（False なら既にあったものを使い回した）。
    :ivar index_status: RAG の索引の状態（応答の値をそのまま）。
    :ivar index_seconds: 索引が出来上がるまでに待った秒数。
    """

    name: str
    id: str
    created: bool
    index_status: str
    index_seconds: float


@dataclass(frozen=True)
class KbSetupResult:
    """`setup-kb` の結果。

    :ivar documents: 登録した（または使い回した）文書。
    :ivar agent_id: C の Agent の id。
    :ivar agent_created: 新規に作ったか（False なら更新）。
    :ivar llm: Agent に設定した LLM。A と同じ値であること。
    :ivar env_written: `.env` の ``ELEVENLABS_KB_AGENT_ID`` を書いたか。
    """

    documents: tuple[KbDocument, ...]
    agent_id: str
    agent_created: bool
    llm: str
    env_written: bool

    def describe(self) -> str:
        """人が読む数行。"""
        lines = [f'Knowledge Base（{RAG_MODEL}）:']
        for doc in self.documents:
            state = '登録' if doc.created else '既存'
            lines.append(
                f'  {doc.name}: {state}  id={doc.id}'
                f'  索引={doc.index_status}（{doc.index_seconds:.0f} 秒）'
            )
        agent_state = '作成' if self.agent_created else '更新'
        lines += [
            f'Agent {KB_AGENT_NAME}: {agent_state}  id={self.agent_id}  llm={self.llm}',
            '  道具: なし（C は知識を ElevenLabs 側に置く構成）',
            f'.env の {KB_AGENT_ID_KEY}: {"更新した" if self.env_written else "変更なし"}',
        ]
        return '\n'.join(lines)


# --------------------------------------------------------------------------- 文書


def corpus_documents(corpus_dir: Path = CORPUS_DIR) -> list[tuple[str, str]]:
    """``corpus/*.md`` を ``(文書名, 本文)`` の一覧にする（ファイル名順）。

    文書名を :func:`voicelab.search.load_sections` と同じ ``path.stem`` にしているのは、
    A と C で**返答に出るノート名を同じにする**ため。ここがずれると、同じ答えでも
    出典の言い方が変わって、正誤の判定がそろわない。
    """
    return [
        (path.stem, path.read_text(encoding='utf-8'))
        for path in sorted(corpus_dir.glob('*.md'))
    ]


def list_documents(client: httpx.Client) -> list[dict]:
    """Knowledge Base の一覧を全ページ集めて返す。"""
    documents: list[dict] = []
    params: dict[str, str | int] = {'page_size': 100}
    while True:
        payload = request_json(client, 'GET', '/convai/knowledge-base', params=params)
        documents.extend(payload.get('documents') or [])
        cursor = payload.get('next_cursor')
        if not payload.get('has_more') or not cursor:
            return documents
        params['cursor'] = cursor


def document_id(documents: list[dict], name: str) -> str | None:
    """一覧から名前で id を探す純粋関数（無ければ None）。"""
    for item in documents:
        if item.get('name') == name:
            for key in ('id', 'documentation_id', 'document_id'):
                value = item.get(key)
                if value:
                    return str(value)
    return None


def ensure_document(
    client: httpx.Client, name: str, text: str, *, recreate: bool = False
) -> tuple[str, bool]:
    """本文を Knowledge Base に置く。戻り値は ``(文書 id, 新しく作ったか)``。

    確認済みの API には**本文を差し替える口が無い**（あるのは登録・一覧・削除）。そこで
    既に同じ名前があれば、既定では**その id をそのまま使う**（索引を張り直して Agent に
    紐付け直すので、設定としては更新される）。``corpus/`` の本文そのものを直したときだけ、
    ``recreate=True``（CLI の ``--recreate``）で消してから登録し直す。
    """
    existing = document_id(list_documents(client), name)
    if existing and not recreate:
        return existing, False
    if existing:
        request_json(client, 'DELETE', f'/convai/knowledge-base/{existing}')
    created = request_json(
        client, 'POST', '/convai/knowledge-base/text', json={'text': text, 'name': name}
    )
    for key in ('id', 'documentation_id', 'document_id'):
        value = created.get(key)
        if value:
            return str(value), True
    raise KbSetupError(f'{name} の登録応答に id がありません: {created}')


# --------------------------------------------------------------------------- 索引


def index_status(payload: dict, model: str = RAG_MODEL) -> str:
    """索引の応答から状態の文字列を取り出す純粋関数（無ければ空）。

    応答の形が版によって違う（``{"status": ...}`` のことも
    ``{"indexes": [{"model": ..., "status": ...}]}`` のこともある）ので、両方見る。
    複数の索引があるときは **:data:`RAG_MODEL` のものだけ**を見る。ほかのモデルの索引が
    出来上がっていても、日本語では引けないため。
    """
    indexes = payload.get('indexes')
    if isinstance(indexes, list) and indexes:
        matched = [i for i in indexes if i.get('model') == model] or indexes
        return str(matched[0].get('status') or '')
    return str(payload.get('status') or '')


def index_state(status: str) -> str:
    """状態の文字列を ``done`` / ``failed`` / ``running`` の 3 つに畳む純粋関数。"""
    normalized = status.strip().lower()
    if normalized in RAG_DONE_STATUSES:
        return 'done'
    if normalized in RAG_FAILED_STATUSES:
        return 'failed'
    return 'running'


def ensure_rag_index(
    client: httpx.Client,
    doc_id: str,
    *,
    model: str = RAG_MODEL,
    timeout_seconds: float = RAG_TIMEOUT_SECONDS,
    poll_seconds: float = RAG_POLL_SECONDS,
    sleep=time.sleep,
    clock=time.monotonic,
) -> tuple[str, float]:
    """索引を作って、出来上がるまで待つ。戻り値は ``(状態, 待った秒数)``。

    ``POST`` は既に索引があっても同じ状態を返すので、そのまま冪等に使える。
    待つのは、索引が出来上がる前に会話を流すと **RAG が空を返して「見当たりません」に
    なってしまう**ため（C の計測が成立しない）。

    ``sleep`` と ``clock`` を差し替えられるようにしてあるのはテストのため。
    実時間を待たずに、待ち続ける / 諦める の分岐だけを確かめられる。

    :raises KbSetupError: 索引が失敗した、または時間内に出来上がらなかった。
    """
    started = clock()
    payload = request_json(
        client, 'POST', f'/convai/knowledge-base/{doc_id}/rag-index', json={'model': model}
    )
    while True:
        status = index_status(payload, model)
        state = index_state(status)
        waited = clock() - started
        if state == 'done':
            return status, waited
        if state == 'failed':
            raise KbSetupError(f'文書 {doc_id} の索引が失敗しました（状態 {status}）。')
        if waited >= timeout_seconds:
            raise KbSetupError(
                f'文書 {doc_id} の索引が {timeout_seconds:.0f} 秒で出来上がりませんでした'
                f'（状態 {status or "不明"}）。少し待って setup-kb をもう一度実行してください。'
            )
        sleep(poll_seconds)
        payload = request_json(client, 'GET', f'/convai/knowledge-base/{doc_id}/rag-index')


# --------------------------------------------------------------------------- Agent


def knowledge_base_entries(documents: list[KbDocument] | tuple[KbDocument, ...]) -> list[dict]:
    """Agent に紐付ける文書の指定を作る純粋関数。

    ``usage_mode`` を ``auto`` にすると、Agent は毎回の応答で必要なときだけ RAG を引く
    （``prompt`` に全文を差し込む ``prompt`` 指定とは違う）。全文を差し込むと prompt が
    毎回ふくらんで、遅延も費用も「RAG の比較」ではなくなる。
    """
    return [
        {'type': 'text', 'name': doc.name, 'id': doc.id, 'usage_mode': 'auto'}
        for doc in documents
    ]


def kb_agent_body(
    *,
    prompt: str,
    llm: str,
    voice_id: str,
    model_id: str,
    documents: list[KbDocument] | tuple[KbDocument, ...],
) -> dict:
    """C の Agent の設定。A との違いは ``tool_ids`` が空なことと、``knowledge_base`` と ``rag`` があることだけ。"""
    return build_agent_body(
        name=KB_AGENT_NAME,
        prompt=prompt,
        llm=llm,
        voice_id=voice_id,
        model_id=model_id,
        tool_ids=[],  # C は道具を持たない。これが A との一番の違い
        prompt_extra={
            'knowledge_base': knowledge_base_entries(documents),
            'rag': {
                'enabled': True,
                'embedding_model': RAG_MODEL,
                'max_retrieved_rag_chunks_count': RAG_MAX_CHUNKS,
            },
        },
    )


def ensure_kb_agent(
    client: httpx.Client,
    *,
    prompt: str,
    llm: str,
    voice_id: str,
    model_id: str,
    documents: list[KbDocument] | tuple[KbDocument, ...],
) -> tuple[str, bool]:
    """C の Agent を作るか更新する。戻り値は ``(agent_id, 作成したか)``。"""
    body = kb_agent_body(
        prompt=prompt, llm=llm, voice_id=voice_id, model_id=model_id, documents=documents
    )
    existing = find_agent(client, KB_AGENT_NAME)
    if existing:
        request_json(client, 'PATCH', f'/convai/agents/{existing}', json=body)
        return existing, False
    created = request_json(client, 'POST', '/convai/agents/create', json=body)
    for key in ('agent_id', 'id'):
        value = created.get(key)
        if value:
            return str(value), True
    raise KbSetupError(f'Agent の作成応答に id がありません: {created}')


def summarize_agent(payload: dict) -> dict:
    """Agent の設定から、C として見たいところだけを取り出す純粋関数。

    ``--dry-run`` が「知識を預けてあるか」「道具が無いか」を会話せずに言えるようにするため。
    """
    prompt = (
        ((payload.get('conversation_config') or {}).get('agent') or {}).get('prompt') or {}
    )
    knowledge_base = prompt.get('knowledge_base') or []
    rag = prompt.get('rag') or {}
    return {
        'name': payload.get('name') or '',
        'llm': prompt.get('llm') or '',
        'documents': [str(item.get('name') or '') for item in knowledge_base],
        'tool_ids': list(prompt.get('tool_ids') or []),
        'rag_enabled': bool(rag.get('enabled')),
        'embedding_model': rag.get('embedding_model') or '',
        'max_chunks': rag.get('max_retrieved_rag_chunks_count'),
    }


def fetch_agent_summary(api_key: str, agent_id: str) -> dict:
    """Agent の設定を 1 回だけ読んで :func:`summarize_agent` にかける。

    会話は開かないのでクレジットは消費しない。``--dry-run`` から呼ぶ。
    """
    headers = {'xi-api-key': api_key, 'accept': 'application/json'}
    with httpx.Client(headers=headers, timeout=TIMEOUT_SECONDS) as client:
        return summarize_agent(request_json(client, 'GET', f'/convai/agents/{agent_id}'))


# --------------------------------------------------------------------------- 入口


def setup(
    env: dict[str, str] | None = None,
    *,
    env_path: Path = ENV_PATH,
    corpus_dir: Path = CORPUS_DIR,
    recreate: bool = False,
) -> KbSetupResult:
    """ノートを預け、索引を張り、C の Agent をこの repo の設定どおりの状態にする。

    会話を開かないのでクレジットは消費しない（索引の作成も無料）。

    :param recreate: 既にある文書を消してから登録し直す。``corpus/`` の本文を直したとき用。
    :raises ConfigError: `.env` に API キーや声の id が無い。
    :raises KbSetupError: ElevenLabs 側が受け付けなかった、または索引が出来上がらなかった。
    """
    env = load_env() if env is None else env
    api_key = require('ELEVENLABS_API_KEY', env)
    voice_id = require('ELEVENLABS_VOICE_ID', env)
    model_id = require('ELEVENLABS_MODEL_ID', env)
    llm = env.get('VOICELAB_LLM') or DEFAULT_LLM
    if not KB_PROMPT_PATH.exists():
        raise ConfigError(f'C の指示が見つかりません: {KB_PROMPT_PATH.name}')
    prompt = KB_PROMPT_PATH.read_text(encoding='utf-8').strip()

    sources = corpus_documents(corpus_dir)
    if not sources:
        raise ConfigError(f'題材ノートがありません: {corpus_dir}')

    headers = {'xi-api-key': api_key, 'accept': 'application/json'}
    with httpx.Client(headers=headers, timeout=TIMEOUT_SECONDS) as client:
        documents: list[KbDocument] = []
        for name, text in sources:
            doc_id, created = ensure_document(client, name, text, recreate=recreate)
            status, waited = ensure_rag_index(client, doc_id)
            documents.append(
                KbDocument(
                    name=name,
                    id=doc_id,
                    created=created,
                    index_status=status,
                    index_seconds=waited,
                )
            )
        agent_id, agent_created = ensure_kb_agent(
            client,
            prompt=prompt,
            llm=llm,
            voice_id=voice_id,
            model_id=model_id,
            documents=documents,
        )
    return KbSetupResult(
        documents=tuple(documents),
        agent_id=agent_id,
        agent_created=agent_created,
        llm=llm,
        env_written=write_agent_id(agent_id, env_path, key=KB_AGENT_ID_KEY),
    )


if __name__ == '__main__':
    # python -m voicelab.kb_setup
    # ノートを預け、索引を張り、C の Agent を作る / 更新する（課金なし）。
    print(setup().describe())
