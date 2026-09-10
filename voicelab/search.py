"""題材ノート（``corpus/``）を見出し単位で引く、依存の無い検索。

この実験で比べたいのは**遅延と費用**であって検索の賢さではない。だからここは
「誰が動かしても同じ結果になる」ことだけを目標にしている。埋め込みも辞書も使わない。

日本語には分かち書きが無いので、単語ではなく**文字のバイグラム**の重なりで並べる。
形態素解析器を入れれば精度は上がるが、環境によって結果が変わるので比較の土台に向かない。
ノートが 3 本しか無いことも、この割り切りを許している。

A（Agents Platform）と B（自前構成）の**両方から同じ関数を呼ぶ**のが存在理由。
検索の質が同じでないと、遅延と費用の差が構成の差だと言えなくなる。
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .config import CORPUS_DIR

#: 返す件数の既定。声で読み上げる前提なので、多くても意味がない。
DEFAULT_LIMIT = 3

#: 抜粋の長さ。LLM に渡す量を抑えつつ、答えるのに足りる長さ。
EXCERPT_CHARS = 300


@dataclass(frozen=True)
class Section:
    """ノートを見出しで切った 1 節。

    :ivar note: ノート名（ファイル名から拡張子を除いたもの）。
    :ivar heading: 見出しの文字列（``#`` は外す）。見出しの前にある本文は ``None``。
    :ivar body: 見出しを含まない本文。
    """

    note: str
    heading: str | None
    body: str

    @property
    def searchable(self) -> str:
        """スコアの対象。ノート名も混ぜる。

        「5月12日の定例で決まったことは」のように、**ノート名でしか手掛かりが無い**質問が
        あるため。見出しと本文だけを見ると、この種の質問が引けない。
        """
        return f'{self.note}\n{self.heading or ""}\n{self.body}'


def split_sections(text: str, note: str) -> list[Section]:
    """Markdown を ``#`` / ``##`` の見出しで節に切る。

    見出しの階層は無視して、見出し行が来たらそこで切るだけにしている。節の大きさが
    読み上げに合う程度に揃えばよく、木構造は要らないため。
    """
    sections: list[Section] = []
    heading: str | None = None
    body: list[str] = []

    def flush() -> None:
        content = '\n'.join(body).strip()
        if heading is not None or content:
            sections.append(Section(note=note, heading=heading, body=content))

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('#') and stripped.lstrip('#').startswith(' '):
            flush()
            heading = stripped.lstrip('#').strip()
            body = []
        else:
            body.append(line)
    flush()
    return sections


def load_sections(corpus_dir: Path = CORPUS_DIR) -> list[Section]:
    """``corpus/*.md`` を全部読んで節の一覧にする（ファイル名順）。"""
    sections: list[Section] = []
    for path in sorted(corpus_dir.glob('*.md')):
        sections.extend(split_sections(path.read_text(encoding='utf-8'), path.stem))
    return sections


def bigrams(text: str) -> set[str]:
    """文字バイグラムの集合。

    空白を落として小文字化するだけの正規化にとどめる。記号を落とすと ``RRF`` のような
    英字の略語や日付が壊れることがあるため。1 文字以下なら空集合（＝スコア 0）。
    """
    normalized = ''.join(text.split()).lower()
    return {normalized[i : i + 2] for i in range(len(normalized) - 1)}


def score_section(query_bigrams: set[str], section: Section) -> int:
    """質問と節で共通するバイグラムの種類数。多いほど上位。"""
    return len(query_bigrams & bigrams(section.searchable))


def search(
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    sections: list[Section] | None = None,
) -> list[dict[str, str | None]]:
    """質問に近い節を上位 ``limit`` 件返す。1 件も重ならなければ空。

    同点のときはノート名と見出しで並べる。実行するたびに順番が変わると、
    A と B の比較にならないため。
    """
    query_bigrams = bigrams(query)
    if not query_bigrams:
        return []
    pool = load_sections() if sections is None else sections
    scored = [(score_section(query_bigrams, s), s) for s in pool]
    hits = [(score, s) for score, s in scored if score > 0]
    hits.sort(key=lambda item: (-item[0], item[1].note, item[1].heading or ''))
    return [
        {
            'note': s.note,
            'heading': s.heading,
            'excerpt': s.body[:EXCERPT_CHARS],
        }
        for _, s in hits[:limit]
    ]


def search_notes(query: str) -> str:
    """LLM に渡すための薄い包み。結果を JSON 文字列にして返す。

    Agent の client tool の戻り値は文字列か辞書だが、**文字列に固定**しておく方が
    どの LLM でも同じ形で読める。``ensure_ascii=False`` は日本語をそのまま出すため。
    """
    return json.dumps({'results': search(query)}, ensure_ascii=False)
