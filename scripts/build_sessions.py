"""
Googleスプレッドシート（ウェブに公開したCSV）から sessions.json を作るスクリプト。

使い方:
    python scripts/build_sessions.py <CSVのURL または ファイルパス> [出力先]

    引数を省略すると、環境変数 SHEET_CSV_URL を使う（GitHub Actions 用）。
    出力先の省略時は sessions.json。

シートの列（1行目が見出し。順番は自由、使わない列は省略可）:
    タイトル / 日付 / 写真 / GM / PL1 / PC1 / PL2 / PC2 / … / 概要 / 感想 / 非表示

    ・PL と PC は番号を増やせばいくらでも追加できる（PL6, PC6 …）
    ・写真は番号（1 → photos/001.png）。「001」「001.png」と書いてもOK。空欄なら写真なし
    ・セル内改行（Alt+Enter / Ctrl+Enter）はそのまま改行として表示される
    ・タイトルが空の行は無視する
    ・「非表示」列にチェック（TRUE）を入れた行は出力しない
"""

import csv
import io
import json
import os
import re
import sys
import unicodedata
import urllib.request

# 見出し名 → JSONのキー（見出しは NFKC 正規化・小文字化して比較する）
HEADER_ALIASES = {
    "title": ["タイトル", "セッション名", "シナリオ名", "title"],
    "date": ["日付", "日時", "開催日", "date"],
    "photo": ["写真", "写真番号", "集合写真", "photo"],
    "gm": ["gm", "gm名"],
    "summary": ["概要", "あらすじ", "summary"],
    "impression": ["感想", "impression"],
    "hidden": ["非表示", "hidden"],
}

PLAYER_HEADER = re.compile(r"^(pl|pc)\s*(\d+)$")
TRUE_VALUES = {"true", "1", "yes", "y", "はい", "○", "◯", "x", "✓", "✔"}


def normalize(text):
    return unicodedata.normalize("NFKC", text or "").strip().lower()


def clean(text):
    """セルの値を整える（改行コードを \\n に揃え、前後の空白を落とす）"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def read_source(source):
    if re.match(r"^https?://", source):
        req = urllib.request.Request(source, headers={"User-Agent": "trpg-session-log-builder"})
        with urllib.request.urlopen(req, timeout=30) as res:
            data = res.read()
    else:
        with open(source, "rb") as f:
            data = f.read()
    return data.decode("utf-8-sig")


def map_headers(headers):
    """列番号 → ('field', key) または ('player', 番号, 'pl'/'pc')"""
    lookup = {}
    for key, names in HEADER_ALIASES.items():
        for name in names:
            lookup[normalize(name)] = key

    columns = {}
    for i, header in enumerate(headers):
        h = normalize(header)
        if h in lookup:
            columns[i] = ("field", lookup[h])
            continue
        m = PLAYER_HEADER.match(h)
        if m:
            columns[i] = ("player", int(m.group(2)), m.group(1))
    return columns


def parse_photo(value, row_no):
    value = normalize(value)
    if not value:
        return None
    m = re.match(r"^0*(\d+)", value)
    if not m or int(m.group(1)) <= 0:
        print(f"警告: {row_no}行目の写真「{value}」は番号として読めないので無視します", file=sys.stderr)
        return None
    return int(m.group(1))


def build_sessions(csv_text):
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        raise ValueError("CSVが空です")

    columns = map_headers(rows[0])
    if not any(c == ("field", "title") for c in columns.values()):
        raise ValueError("見出し行に「タイトル」列が見つかりません。1行目が見出しになっているか確認してください")

    sessions = []
    for row_no, row in enumerate(rows[1:], start=2):
        fields = {}
        players = {}
        for i, value in enumerate(row):
            col = columns.get(i)
            if col is None:
                continue
            if col[0] == "field":
                fields[col[1]] = value
            else:
                _, number, kind = col
                players.setdefault(number, {})[kind] = clean(value)

        title = clean(fields.get("title"))
        if not title:
            continue
        if normalize(fields.get("hidden")) in TRUE_VALUES:
            continue

        session = {"title": title, "date": clean(fields.get("date"))}

        photo = parse_photo(fields.get("photo"), row_no)
        if photo is not None:
            session["photo"] = photo

        session["gm"] = clean(fields.get("gm"))
        session["players"] = [
            {"pl": p.get("pl", ""), "pc": p.get("pc", "")}
            for _, p in sorted(players.items())
            if p.get("pl") or p.get("pc")
        ]
        session["summary"] = clean(fields.get("summary"))
        session["impression"] = clean(fields.get("impression"))
        sessions.append(session)

    return sessions


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SHEET_CSV_URL", "")
    output = sys.argv[2] if len(sys.argv) > 2 else "sessions.json"

    if not source:
        print("エラー: CSVのURLが指定されていません（引数か環境変数 SHEET_CSV_URL）", file=sys.stderr)
        return 1

    try:
        sessions = build_sessions(read_source(source))
    except Exception as e:  # 読み込み失敗時は今の sessions.json を残す
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    # 事故防止: シートが空になったように見えるときは上書きしない
    if not sessions and os.path.exists(output):
        print("エラー: シートから1件も読み取れなかったので、sessions.json は更新しません", file=sys.stderr)
        return 1

    text = json.dumps({"sessions": sessions}, ensure_ascii=False, indent=2) + "\n"

    old = None
    if os.path.exists(output):
        with open(output, encoding="utf-8-sig") as f:
            old = f.read()

    if old == text:
        print(f"変更なし（{len(sessions)} 件）")
        return 0

    with open(output, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"{output} を更新しました（{len(sessions)} 件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
