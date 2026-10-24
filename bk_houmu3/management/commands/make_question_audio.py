"""問題と解説の読み上げ音声を Azure AI Speech で作る。有効な問題だけが対象。

1問につき「問題文と選択肢」と「解説」の2本を作る。音声がまだ無いものだけを作る。
文が変わっていないものは作り直さないので、問題を取り込み直したあとにもう一度
流せば、直した問題・解説の分だけ文字数を使う。
キーは環境変数 AZURE_SPEECH_KEY と AZURE_SPEECH_REGION から読む。

    python manage.py make_question_audio --dry-run   # 作る本数と文字数だけ表示する
    python manage.py make_question_audio --limit 3   # 3本だけ作る（試聴用）
    python manage.py make_question_audio             # 残りを全部作る
    python manage.py make_question_audio --prune     # どの問題にも使われていない音声を消す
"""

import os
import time

from django.core.management.base import BaseCommand, CommandError

from bk_houmu3 import speech
from bk_houmu3.models import Question


class Command(BaseCommand):
    help = '問題と解説の読み上げ音声を作る（まだ無いものだけ）。'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, help='作る本数の上限。')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='作らずに、作る本数と使う文字数だけを表示する。',
        )
        parser.add_argument(
            '--prune', action='store_true',
            help='有効などの問題にも使われていない音声ファイルを消す。',
        )

    def handle(self, *args, **options):
        questions = Question.objects.filter(is_active=True)
        # 同じ文の問題が2つあっても、音声は1本で足りる
        texts = list(dict.fromkeys(
            text for question in questions for text in speech.texts(question)
        ))
        if options['prune']:
            self._prune(texts, options['dry_run'])
            return

        missing = [text for text in texts if not speech.has_audio(text)]
        if options['limit'] is not None:
            missing = missing[:options['limit']]
        chars = sum(speech.billed_chars(text) for text in missing)
        self.stdout.write('問題 {} 問・音声 {} 本のうち、作るのは {} 本（約 {:,} 字）。'.format(
            questions.count(), len(texts), len(missing), chars,
        ))
        if options['dry_run'] or not missing:
            return

        key = os.environ.get('AZURE_SPEECH_KEY')
        region = os.environ.get('AZURE_SPEECH_REGION')
        if not key or not region:
            raise CommandError('環境変数 AZURE_SPEECH_KEY と AZURE_SPEECH_REGION を設定してください。')

        used = 0
        for index, text in enumerate(missing, 1):
            if index > 1:
                # 音声の間も、回数の上限に当たらないよう間を空ける
                time.sleep(speech.REQUEST_INTERVAL)
            used += speech.build_audio(text, key, region)
            self.stdout.write('[{}/{}] {}'.format(index, len(missing), text[:30].replace('\n', ' ')))
        self.stdout.write(self.style.SUCCESS(
            '{} 本作りました（{:,} 字）。保存先: {}'.format(len(missing), used, speech.audio_root())
        ))

    def _prune(self, texts, dry_run):
        root = speech.audio_root()
        if not root.is_dir():
            self.stdout.write('音声のフォルダがありません。')
            return
        used = {speech.audio_name(text) for text in texts}
        stale = [p for p in root.iterdir() if p.is_file() and p.name not in used]
        for path in stale:
            if not dry_run:
                path.unlink()
        self.stdout.write('使われていない音声: {} 個{}'.format(
            len(stale), '（消していません）' if dry_run else 'を消しました',
        ))
