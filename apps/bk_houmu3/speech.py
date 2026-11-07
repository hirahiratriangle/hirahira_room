"""問題と解説の読み上げ音声。Azure AI Speech で問題ごとに MP3 を作って置いておく。

基本情報版（fe/speech.py）の作りに合わせた。基本情報版は学習モードの技術解説を
読むが、法務3級は技術解説を持たないので、1問1答の「問題文と選択肢」（解答する前）と
「解説」（解答した後）を読む。1問につき音声は2本になる。

開くたびに合成すると、そのたびに文字数を使う。問題は取り込み直すまで変わらない
ので、一度だけ作って保存し、画面からはそのファイルを流す。

ファイル名は「声・音質・読ませる文」から計算したハッシュにする。文が同じなら
同じ名前になるので作り直さず、直した問題・解説だけ新しい名前で作られる。
どの問題がどのファイルかは DB に持たない（取り込みで問題が入れ替わっても、
名前を計算し直せば足りる）。

作るのは管理コマンド make_question_audio。キーは環境変数 AZURE_SPEECH_KEY と
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
from django.urls import reverse

VOICE = 'ja-JP-NanamiNeural'
# 読み上げなので、モノラル 48kbps で足りる（1分あたり約360KB）
OUTPUT_FORMAT = 'audio-24khz-48kbitrate-mono-mp3'
# 1回の依頼で作れる音声は10分まで。長い文は段落の切れ目で分けて頼み、つなぐ。
# 約280字で1分なので、1200字なら4分少々に収まる。
CHUNK_CHARS = 1200
# 無料プラン（F0）は1分あたりの依頼回数に上限があるので、依頼の間を空ける
REQUEST_INTERVAL = 3.5

# 読み違える語と、その読み。聴いて気になったものを足す。
READINGS = {
    '～': 'から',
}

NAME_PATTERN = re.compile(r'^[0-9a-f]{20}\.mp3$')

# 解説の「（2）は正しく」の選択肢の番号。かっこまで読ませず、番号だけを読む。
_CHOICE_NUMBER = re.compile(r'[（(]([1-9])[）)]')


def spoken_text(text):
    """表示用の文を、読み上げ用の文にする。読み違える語を直し、選択肢の番号のかっこを外す。"""
    for word, reading in READINGS.items():
        text = text.replace(word, reading)
    return _CHOICE_NUMBER.sub(r'\1', text)


def question_text(question):
    """解答する前に読む文。問題文のあと、選択肢を番号を付けて1つずつ読む。"""
    lines = [question.stem.strip()]
    for index, choice in enumerate(question.choices, 1):
        lines.append('{}、{}'.format(index, choice))
    return '\n\n'.join(lines)


def explanation_text(question):
    """解答した後に読む文。解説が無ければ空。"""
    return question.explanation.strip()


def texts(question):
    """この問題の音声にする文（空のものは除く）。"""
    return [text for text in (question_text(question), explanation_text(question)) if text]


def chunks(text, limit=CHUNK_CHARS):
    """段落の切れ目で、limit 字以内のまとまりに分ける。

    解説は1行が1段落なので、空行に加えて改行でも分けられるようにする。
    1段落だけで limit を超えるときは、その段落を1つのまとまりにする
    （段落はいちばん長いもので600字台なので、まず起きない）。
    """
    parts, current = [], ''
    for paragraph in re.split(r'\n+', text.strip()):
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


def billed_chars(text):
    """この文の音声を作るときに使う文字数（実際に送る文の字数）。"""
    return sum(len(part) for part in chunks(spoken_text(text)))


def audio_name(text):
    """この文の音声ファイル名。声・音質・読ませる文が同じなら同じ名前になる。"""
    source = '\n'.join([VOICE, OUTPUT_FORMAT, spoken_text(text)])
    return hashlib.sha256(source.encode('utf-8')).hexdigest()[:20] + '.mp3'


def audio_root():
    return Path(settings.BK_HOUMU3_AUDIO_ROOT)


def audio_path(text):
    return audio_root() / audio_name(text)


def has_audio(text):
    return bool(text) and audio_path(text).is_file()


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


def build_audio(text, key, region, interval=REQUEST_INTERVAL):
    """この文の音声を作って保存し、使った文字数を返す。

    まとまりごとに合成した MP3 をそのまま続けて書く（同じ形式の MP3 は
    つなげても再生できる）。書きかけのファイルが残らないよう、別名で書いてから
    置き換える。interval はまとまりの間に空ける秒数（回数の上限に当たらないため）。
    """
    path = audio_path(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.part')
    used = 0
    with open(temp, 'wb') as out:
        for index, part in enumerate(chunks(spoken_text(text))):
            if index:
                time.sleep(interval)
            out.write(synthesize(part, key, region))
            used += len(part)
    temp.replace(path)
    return used


def audio_url(text):
    """画面に出す再生ボタンの音声の URL。音声がまだ無ければ None。"""
    return reverse('bk_houmu3:question_audio', args=[audio_name(text)]) if has_audio(text) else None
