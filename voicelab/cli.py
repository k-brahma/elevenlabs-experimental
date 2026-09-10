"""コマンドラインの入口。

- ``credits`` … 今の残クレジットを表示（課金されない）
- ``scenarios`` … 質問集を表示
- ``report`` … 記録済みの結果から表を作る
- ``run`` … （未実装）構成を指定して質問集を流す。会話の実装ができてから
"""

import argparse
import json
import sys

from . import credits, metrics
from .config import SCENARIOS_PATH, ConfigError, require


def cmd_credits(_args: argparse.Namespace) -> int:
    snapshot = credits.read_subscription(require('ELEVENLABS_API_KEY'))
    print(snapshot.describe())
    return 0


def cmd_scenarios(_args: argparse.Namespace) -> int:
    data = json.loads(SCENARIOS_PATH.read_text(encoding='utf-8'))
    for item in data['questions']:
        print(f'{item["id"]:<12} {item["text"]}   → {item["expected_note"]}')
    print(f'\n{len(data["questions"])} 問。題材: {data["corpus"]}')
    return 0


def cmd_report(_args: argparse.Namespace) -> int:
    runs = metrics.load_runs()
    path = metrics.write_report(runs)
    print(metrics.render_report(runs))
    print(f'→ {path}')
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    print(
        f'run（{args.path}）はまだ実装していません。'
        ' 会話の実装ができるまで、クレジットを消費する処理は入れない方針です。',
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='voicelab', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('credits', help='今の残クレジットを表示').set_defaults(func=cmd_credits)
    sub.add_parser('scenarios', help='質問集を表示').set_defaults(func=cmd_scenarios)
    sub.add_parser('report', help='記録から表を作る').set_defaults(func=cmd_report)

    run = sub.add_parser('run', help='（未実装）質問集を流して計測する')
    run.add_argument('path', choices=metrics.PATHS, help='agents = A, custom = B')
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, credits.CreditsError) as exc:
        print(f'エラー: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
