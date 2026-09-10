"""検索が「誰が動かしても同じ結果」になることを確かめる。

ネットワークは叩かない。題材は ``corpus/`` の実物を使う（これが比較の土台なので、
中身が変わったら気づきたい）。
"""

import json

from voicelab import search


def test_split_sections_見出しごとに切れる():
    text = '# 表題\n\n前書き\n\n## 一つ目\n\n本文 A\n\n## 二つ目\n\n本文 B\n'
    sections = search.split_sections(text, 'ノート')
    assert [s.heading for s in sections] == ['表題', '一つ目', '二つ目']
    assert sections[1].body == '本文 A'
    assert all(s.note == 'ノート' for s in sections)


def test_split_sections_見出しの前の本文は見出し無しの節になる():
    sections = search.split_sections('冒頭の一行\n\n# 表題\n\n本文\n', 'ノート')
    assert sections[0].heading is None
    assert sections[0].body == '冒頭の一行'


def test_split_sections_シャープだけの行は見出しにしない():
    sections = search.split_sections('# 表題\n\n#ハッシュタグ っぽい行\n', 'ノート')
    assert len(sections) == 1
    assert '#ハッシュタグ' in sections[0].body


def test_bigrams_空白を落として二文字ずつ():
    assert search.bigrams('あ い う') == {'あい', 'いう'}
    assert search.bigrams('あ') == set()
    assert search.bigrams('') == set()


def test_bigrams_英字は小文字に揃える():
    assert search.bigrams('RRF') == search.bigrams('rrf')


def test_score_section_重なったバイグラムの数():
    section = search.Section(note='用語集', heading='RRF', body='順位を混ぜる方法')
    assert search.score_section(search.bigrams('RRF'), section) == 2
    assert search.score_section(search.bigrams('まったく別'), section) == 0


def test_search_期待したノートが一位に来る():
    cases = {
        'デプロイの前に確認することを教えて': 'デプロイ手順',
        'ロールバックはどうやるの': 'デプロイ手順',
        'RRF って何のこと': '用語集',
        '5月12日の定例で決まったことは': '2026-05-12 定例メモ',
    }
    for query, expected in cases.items():
        hits = search.search(query)
        assert hits, query
        assert hits[0]['note'] == expected, query


def test_search_ノートに無いことは空で返す():
    assert search.search('来週の天気はどう') == []


def test_search_件数の上限を守る():
    assert len(search.search('デプロイ', limit=2)) <= 2


def test_search_一文字の質問は空():
    assert search.search('あ') == []


def test_search_抜粋は三百文字まで():
    sections = [search.Section(note='長いノート', heading='見出し', body='あ' * 500)]
    hits = search.search('あああ', sections=sections)
    assert len(hits[0]['excerpt']) == search.EXCERPT_CHARS


def test_search_同点なら並びが安定する():
    query = 'デプロイ'
    assert search.search(query) == search.search(query)


def test_search_notes_は日本語のままの_json():
    payload = search.search_notes('ロールバックはどうやるの')
    assert 'デプロイ手順' in payload  # ensure_ascii=False
    parsed = json.loads(payload)
    assert parsed['results'][0]['note'] == 'デプロイ手順'
    assert set(parsed['results'][0]) == {'note', 'heading', 'excerpt'}


def test_search_notes_見つからなければ空の配列():
    assert json.loads(search.search_notes('来週の天気はどう')) == {'results': []}
