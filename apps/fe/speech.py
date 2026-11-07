"""技術解説の読み上げ音声。Azure AI Speech で解説ごとに MP3 を作って置いておく。

開くたびに合成すると、そのたびに文字数を使う。解説は取り込み直すまで変わらない
ので、一度だけ作って保存し、画面からはそのファイルを流す。

ファイル名は「声・音質・読ませる文」から計算したハッシュにする。本文が同じなら
同じ名前になるので作り直さず、本文を直した解説だけ新しい名前で作られる。
どの解説がどのファイルかは DB に持たない（取り込みで解説が入れ替わっても、
名前を計算し直せば足りる）。

読み上げるのは科目Aの解説だけ。

作るのは管理コマンド make_note_audio。キーは環境変数 AZURE_SPEECH_KEY と
AZURE_SPEECH_REGION から読む。
"""

import hashlib
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings

from .models import SUBJECT_A

VOICE = 'ja-JP-NanamiNeural'
# 読み上げなので、モノラル 48kbps で足りる（1分あたり約360KB）
OUTPUT_FORMAT = 'audio-24khz-48kbitrate-mono-mp3'
# 1回の依頼で作れる音声は10分まで。長い解説は段落の切れ目で分けて頼み、つなぐ。
# 約280字で1分なので、1200字なら4分少々に収まる。
CHUNK_CHARS = 1200
# 無料プラン（F0）は1分あたりの依頼回数に上限があるので、依頼の間を空ける
REQUEST_INTERVAL = 3.5

# 読み違える語と、その読み。聴いて気になったものを足す。
READINGS = {
    'C++': 'シープラスプラス',
}

NAME_PATTERN = re.compile(r'^[0-9a-f]{20}\.mp3$')

# 数・英字・漢字・閉じかっこの後の記号は演算（ひく・たす）、それ以外は符号として読む。
# ひらがなは除く（「相関係数は−1」の「は」の後は符号）
_OPERAND_END = r'[0-9A-Za-z一-龠)）]'


def spoken_text(body):
    """本文を、読み上げ用の文にする。記号を言葉に置き換え、読み違える語を直す。"""
    text = body
    for word, reading in READINGS.items():
        text = text.replace(word, reading)
    text = text.replace('×', 'かける').replace('÷', 'わる').replace('＝', 'イコール')
    text = re.sub(r'(?<={})−'.format(_OPERAND_END), 'ひく', text)
    text = text.replace('−', 'マイナス')
    text = re.sub(r'(?<={})＋'.format(_OPERAND_END), 'たす', text)
    text = text.replace('＋', 'プラス')
    return text


def chunks(text, limit=CHUNK_CHARS):
    """段落の切れ目で、limit 字以内のまとまりに分ける。

    1段落だけで limit を超えるときは、その段落を1つのまとまりにする
    （解説の段落はいちばん長いもので400字台なので、まず起きない）。
    """
    parts, current = [], ''
    for paragraph in re.split(r'\n\s*\n', text.strip()):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) + 1 > limit:
            parts.append(current)
            current = paragraph
        else:
            current = '{}\n{}'.format(current, paragraph) if current else paragraph
    if current:
        parts.append(current)
    return parts


def speaks(note):
    """この解説を読み上げるか。科目Aの解説だけ。"""
    return note.category.subject == SUBJECT_A


def billed_chars(note):
    """この解説の音声を作るときに使う文字数（実際に送る文の字数）。"""
    return sum(len(part) for part in chunks(spoken_text(note.body)))


def audio_name(note):
    """この解説の音声ファイル名。声・音質・読ませる文が同じなら同じ名前になる。"""
    source = '\n'.join([VOICE, OUTPUT_FORMAT, spoken_text(note.body)])
    return hashlib.sha256(source.encode('utf-8')).hexdigest()[:20] + '.mp3'


def audio_root():
    return Path(settings.FE_AUDIO_ROOT)


def audio_path(note):
    return audio_root() / audio_name(note)


def has_audio(note):
    return speaks(note) and audio_path(note).is_file()


def ssml(text):
    return (
        "<speak version='1.0' xml:lang='ja-JP'><voice name='{}'>{}</voice></speak>"
        .format(VOICE, escape(text))
    )


def synthesize(text, key, region, retries=5):
    """1まとまりの文を音声にして、MP3 のバイト列を返す。

    無料プラン（F0）は1分あたりの依頼回数に上限があり、超えると 429 が返る。
    そのときは指示された秒数（無ければ10秒）待って頼み直す。
    """
    request = urllib.request.Request(
        'https://{}.tts.speech.microsoft.com/cognitiveservices/v1'.format(region),
        data=ssml(text).encode('utf-8'),
        headers={
            'Ocp-Apim-Subscription-Key': key,
            'Content-Type': 'application/ssml+xml',
            'X-Microsoft-OutputFormat': OUTPUT_FORMAT,
            'User-Agent': 'hirahira-room',
        },
        method='POST',
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == retries - 1:
                raise
            time.sleep(int(error.headers.get('Retry-After') or 10))


def build_audio(note, key, region, interval=REQUEST_INTERVAL):
    """この解説の音声を作って保存し、使った文字数を返す。

    まとまりごとに合成した MP3 をそのまま続けて書く（同じ形式の MP3 は
    つなげても再生できる）。書きかけのファイルが残らないよう、別名で書いてから
    置き換える。interval はまとまりの間に空ける秒数（回数の上限に当たらないため）。
    """
    path = audio_path(note)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.part')
    used = 0
    with open(temp, 'wb') as out:
        for index, part in enumerate(chunks(spoken_text(note.body))):
            if index:
                time.sleep(interval)
            out.write(synthesize(part, key, region))
            used += len(part)
    temp.replace(path)
    return used
