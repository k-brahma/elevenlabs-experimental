"""C（Knowledge Base）の登録・索引・Agent 作成。

**ネットワークを叩かない。** ElevenLabs の REST は :class:`httpx.MockTransport` で受けて、
送った body と呼んだ順番を確かめる。索引の待ちは偽の時計と偽の ``sleep`` で回すので、
テストが 2 分待つことはない。

ここで守りたいのは 3 つ。

- 名前を鍵にして冪等（無ければ作り、あれば使い回して更新する）
- 索引の埋め込みが日本語向き（``multilingual_e5_large_instruct``）で固定されている
- C の Agent が**道具を持たない**（``tool_ids`` が空）
"""

import json

import httpx
import pytest

from voicelab import kb_setup
from voicelab.config import ConfigError
from voicelab.kb_setup import KbDocument, KbSetupError

#: 素の ``httpx.Client``。テストの途中で ``kb_setup.httpx.Client`` を差し替えるので、
#: 差し替え前の本物をここに控えておく（控えないと偽 client が自分を呼んで無限に沈む）。
REAL_CLIENT = httpx.Client


class FakeApi:
    """ElevenLabs の convai を、必要なところだけ真似る。

    :param existing_documents: 既に登録済みの文書名。
    :param existing_agent: ``voicelab-c`` が既にあるか。
    :param index_statuses: 索引の状態を返す順番。最後の値を返し続ける。
    """

    def __init__(self, *, existing_documents=(), existing_agent=False, index_statuses=('succeeded',)):
        self.documents = {name: f'doc_{index}' for index, name in enumerate(existing_documents)}
        self.existing_agent = existing_agent
        self.index_statuses = list(index_statuses)
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict] = []
        self._created = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self) -> httpx.Client:
        return REAL_CLIENT(transport=self.transport(), base_url='https://api.elevenlabs.io')

    def _next_status(self) -> str:
        return self.index_statuses.pop(0) if len(self.index_statuses) > 1 else self.index_statuses[0]

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.replace('/v1', '', 1)
        self.calls.append((request.method, path))
        body = json.loads(request.content) if request.content else {}
        if body:
            self.bodies.append(body)

        if path == '/convai/knowledge-base' and request.method == 'GET':
            return httpx.Response(
                200,
                json={
                    'documents': [{'id': i, 'name': n} for n, i in self.documents.items()],
                    'has_more': False,
                },
            )
        if path == '/convai/knowledge-base/text' and request.method == 'POST':
            self._created += 1
            new_id = f'new_{self._created}'
            self.documents[body['name']] = new_id
            return httpx.Response(200, json={'id': new_id, 'name': body['name']})
        if path.startswith('/convai/knowledge-base/') and request.method == 'DELETE':
            doc_id = path.rsplit('/', 1)[-1]
            self.documents = {n: i for n, i in self.documents.items() if i != doc_id}
            return httpx.Response(200, json={})
        if path.endswith('/rag-index'):
            return httpx.Response(
                200, json={'indexes': [{'model': kb_setup.RAG_MODEL, 'status': self._next_status()}]}
            )
        if path == '/convai/agents' and request.method == 'GET':
            agents = [{'agent_id': 'agent_c', 'name': kb_setup.KB_AGENT_NAME}] if self.existing_agent else []
            return httpx.Response(200, json={'agents': agents, 'has_more': False})
        if path == '/convai/agents/create':
            return httpx.Response(200, json={'agent_id': 'agent_new'})
        if path.startswith('/convai/agents/') and request.method == 'PATCH':
            return httpx.Response(200, json={'agent_id': path.rsplit('/', 1)[-1]})
        return httpx.Response(404, json={'detail': f'未対応: {path}'})


@pytest.fixture
def env(tmp_path):
    return {
        'ELEVENLABS_API_KEY': 'key',
        'ELEVENLABS_VOICE_ID': 'voice',
        'ELEVENLABS_MODEL_ID': 'eleven_flash_v2_5',
        'VOICELAB_LLM': 'gemini-3.6-flash',
    }


def _run_setup(api: FakeApi, monkeypatch, env, tmp_path, **kwargs):
    """``setup()`` を偽の API につないで走らせる。"""
    monkeypatch.setattr(kb_setup.httpx, 'Client', lambda **_: api.client())
    return kb_setup.setup(env, env_path=tmp_path / '.env', **kwargs)


def test_setup_全部新規なら本文を三本登録して_Agent_を作る(monkeypatch, env, tmp_path):
    api = FakeApi()
    result = _run_setup(api, monkeypatch, env, tmp_path)

    assert [doc.name for doc in result.documents] == ['2026-05-12 定例メモ', 'デプロイ手順', '用語集']
    assert all(doc.created for doc in result.documents)
    assert result.agent_created is True
    assert result.agent_id == 'agent_new'
    assert ('POST', '/convai/knowledge-base/text') in api.calls
    assert api.calls.count(('POST', '/convai/knowledge-base/text')) == 3


def test_setup_既にあれば作り直さず更新する(monkeypatch, env, tmp_path):
    api = FakeApi(
        existing_documents=('2026-05-12 定例メモ', 'デプロイ手順', '用語集'), existing_agent=True
    )
    result = _run_setup(api, monkeypatch, env, tmp_path)

    assert not any(doc.created for doc in result.documents)
    assert result.agent_created is False
    assert result.agent_id == 'agent_c'
    assert ('POST', '/convai/knowledge-base/text') not in api.calls
    assert ('PATCH', '/convai/agents/agent_c') in api.calls


def test_setup_recreate_なら消してから登録し直す(monkeypatch, env, tmp_path):
    api = FakeApi(existing_documents=('デプロイ手順',))
    _run_setup(api, monkeypatch, env, tmp_path, recreate=True)
    assert ('DELETE', '/convai/knowledge-base/doc_0') in api.calls


def test_setup_は_Agent_に道具を持たせず日本語の埋め込みを使う(monkeypatch, env, tmp_path):
    api = FakeApi()
    _run_setup(api, monkeypatch, env, tmp_path)

    agent_body = [b for b in api.bodies if b.get('name') == kb_setup.KB_AGENT_NAME][-1]
    prompt = agent_body['conversation_config']['agent']['prompt']
    assert prompt['tool_ids'] == []
    assert prompt['rag'] == {
        'enabled': True,
        'embedding_model': 'multilingual_e5_large_instruct',
        'max_retrieved_rag_chunks_count': 3,
    }
    assert [item['name'] for item in prompt['knowledge_base']] == [
        '2026-05-12 定例メモ',
        'デプロイ手順',
        '用語集',
    ]
    assert all(item['usage_mode'] == 'auto' for item in prompt['knowledge_base'])


def test_setup_の_Agent_設定は_A_と同じ値を使う(monkeypatch, env, tmp_path):
    """声・TTS・turn・client_events・認証がずれていないこと（比較の条件を揃えるため）。"""
    from voicelab import agent_setup

    api = FakeApi()
    _run_setup(api, monkeypatch, env, tmp_path)
    c = [b for b in api.bodies if b.get('name') == kb_setup.KB_AGENT_NAME][-1]
    a = agent_setup._agent_body(
        prompt='なんでもよい', llm='gemini-3.6-flash', voice_id='voice',
        model_id='eleven_flash_v2_5', tool_id='tool_x',
    )
    for section in ('tts', 'turn', 'conversation'):
        assert c['conversation_config'][section] == a['conversation_config'][section]
    assert c['platform_settings'] == a['platform_settings']
    assert c['conversation_config']['agent']['first_message'] == ''
    assert c['conversation_config']['agent']['language'] == 'ja'


def test_setup_は_env_に_KB_の_agent_id_を書く(monkeypatch, env, tmp_path):
    env_path = tmp_path / '.env'
    env_path.write_text('ELEVENLABS_API_KEY=xxx\nELEVENLABS_AGENT_ID=agent_a\n', encoding='utf-8')
    api = FakeApi()
    monkeypatch.setattr(kb_setup.httpx, 'Client', lambda **_: api.client())
    result = kb_setup.setup(env, env_path=env_path)

    text = env_path.read_text(encoding='utf-8')
    assert result.env_written is True
    assert 'ELEVENLABS_KB_AGENT_ID=agent_new' in text
    assert 'ELEVENLABS_AGENT_ID=agent_a' in text  # A の行には触らない
    assert 'ELEVENLABS_API_KEY=xxx' in text


def test_ensure_rag_index_は出来上がるまで待つ():
    api = FakeApi(index_statuses=['processing', 'processing', 'succeeded'])
    waits: list[float] = []
    status, seconds = kb_setup.ensure_rag_index(
        api.client(), 'doc_1', sleep=waits.append, clock=iter([0.0, 0.0, 2.0, 4.0]).__next__
    )
    assert status == 'succeeded'
    assert waits == [kb_setup.RAG_POLL_SECONDS, kb_setup.RAG_POLL_SECONDS]
    assert seconds == 4.0


def test_ensure_rag_index_は待ちきれなければ止まる():
    api = FakeApi(index_statuses=['processing'])
    clock = iter([0.0, 10.0, 130.0])
    with pytest.raises(KbSetupError) as exc:
        kb_setup.ensure_rag_index(api.client(), 'doc_1', sleep=lambda _s: None, clock=clock.__next__)
    assert 'setup-kb' in str(exc.value)


def test_ensure_rag_index_は失敗をそのまま伝える():
    api = FakeApi(index_statuses=['failed'])
    with pytest.raises(KbSetupError):
        kb_setup.ensure_rag_index(api.client(), 'doc_1', sleep=lambda _s: None)


def test_setup_は鍵が無ければ何も呼ばずに止まる(tmp_path):
    with pytest.raises(ConfigError) as exc:
        kb_setup.setup({}, env_path=tmp_path / '.env')
    assert 'ELEVENLABS_API_KEY' in str(exc.value)


def test_corpus_documents_はファイル名から文書名を作る():
    names = [name for name, _ in kb_setup.corpus_documents()]
    assert names == ['2026-05-12 定例メモ', 'デプロイ手順', '用語集']
    assert all(text.strip() for _, text in kb_setup.corpus_documents())


def test_index_status_は該当モデルの状態を選ぶ():
    payload = {
        'indexes': [
            {'model': 'e5_mistral_7b_instruct', 'status': 'failed'},
            {'model': kb_setup.RAG_MODEL, 'status': 'succeeded'},
        ]
    }
    assert kb_setup.index_status(payload) == 'succeeded'
    assert kb_setup.index_status({'status': 'processing'}) == 'processing'
    assert kb_setup.index_status({}) == ''


def test_index_state_は知らない綴りを途中扱いにする():
    assert kb_setup.index_state('SUCCEEDED') == 'done'
    assert kb_setup.index_state('failed') == 'failed'
    assert kb_setup.index_state('なにこれ') == 'running'


def test_index_state_は_created_を出来上がりにしない():
    """実測の順は created → processing → succeeded。created で進むと索引が空のまま測ってしまう。"""
    assert kb_setup.index_state('created') == 'running'
    assert kb_setup.index_state('processing') == 'running'


def test_summarize_agent_は預けた文書と道具の有無を取り出す():
    summary = kb_setup.summarize_agent(
        {
            'name': 'voicelab-c',
            'conversation_config': {
                'agent': {
                    'prompt': {
                        'llm': 'gemini-3.6-flash',
                        'tool_ids': [],
                        'knowledge_base': [{'name': 'デプロイ手順', 'id': 'doc_1'}],
                        'rag': {'enabled': True, 'embedding_model': kb_setup.RAG_MODEL,
                                'max_retrieved_rag_chunks_count': 3},
                    }
                }
            },
        }
    )
    assert summary['documents'] == ['デプロイ手順']
    assert summary['tool_ids'] == []
    assert summary['rag_enabled'] is True
    assert summary['embedding_model'] == kb_setup.RAG_MODEL


def test_summarize_agent_は設定が空でも落ちない():
    summary = kb_setup.summarize_agent({})
    assert summary['documents'] == []
    assert summary['rag_enabled'] is False


def test_knowledge_base_entries_は_auto_で紐付ける():
    entries = kb_setup.knowledge_base_entries(
        [KbDocument(name='用語集', id='doc_9', created=True, index_status='succeeded', index_seconds=1.0)]
    )
    assert entries == [{'type': 'text', 'name': '用語集', 'id': 'doc_9', 'usage_mode': 'auto'}]
