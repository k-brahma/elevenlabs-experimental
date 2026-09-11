"""`python run.py credits` の入口。

``voicelab/cli.py`` を直接 `python voicelab/cli.py` で叩くと、中の相対 import
（``from . import ...``）が解決できずに落ちる。パッケージの一部として読ませる必要が
あるためで、この 1 枚を repo の直上に置いておけば ``python run.py <サブコマンド>`` が
そのまま通る。

``python -m voicelab ...`` と中身は同じ。好きな方を使う。
"""

from voicelab.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
