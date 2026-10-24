"""技術解説の読み上げ音声を Azure AI Speech で作る。科目Aの解説だけが対象。

音声がまだ無い解説だけを作る。本文が変わっていない解説は作り直さないので、
解説を取り込み直したあとにもう一度流せば、直した解説の分だけ文字数を使う。
キーは環境変数 AZURE_SPEECH_KEY と AZURE_SPEECH_REGION から読む。

    python manage.py make_note_audio --dry-run   # 作る本数と文字数だけ表示する
    python manage.py make_note_audio --limit 3   # 3本だけ作る（試聴用）
    python manage.py make_note_audio             # 残りを全部作る
    python manage.py make_note_audio --prune     # どの解説にも使われていない音声を消す
"""

import os
import time

from django.core.management.base import BaseCommand, CommandError

from fe import speech
from fe.models import LearningNote


class Command(BaseCommand):
    help = '科目Aの技術解説の読み上げ音声を作る（まだ無いものだけ）。'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, help='作る本数の上限。')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='作らずに、作る本数と使う文字数だけを表示する。',
        )
        parser.add_argument(
            '--prune', action='store_true',
            help='科目Aのどの解説にも使われていない音声ファイルを消す。',
        )

    def handle(self, *args, **options):
        notes = [n for n in LearningNote.objects.select_related('category') if speech.speaks(n)]
        if options['prune']:
            self._prune(notes, options['dry_run'])
            return

        missing = [n for n in notes if not speech.has_audio(n)]
        if options['limit'] is not None:
            missing = missing[:options['limit']]
        chars = sum(speech.billed_chars(n) for n in missing)
        self.stdout.write('科目Aの解説 {} 本のうち、作るのは {} 本（約 {:,} 字）。'.format(
            len(notes), len(missing), chars,
        ))
        if options['dry_run'] or not missing:
            return

        key = os.environ.get('AZURE_SPEECH_KEY')
        region = os.environ.get('AZURE_SPEECH_REGION')
        if not key or not region:
            raise CommandError('環境変数 AZURE_SPEECH_KEY と AZURE_SPEECH_REGION を設定してください。')

        used = 0
        for index, note in enumerate(missing, 1):
            if index > 1:
                # 解説の間も、回数の上限に当たらないよう間を空ける
                time.sleep(speech.REQUEST_INTERVAL)
            used += speech.build_audio(note, key, region)
            self.stdout.write('[{}/{}] {}. {}'.format(
                index, len(missing), note.category.code, note.title,
            ))
        self.stdout.write(self.style.SUCCESS(
            '{} 本作りました（{:,} 字）。保存先: {}'.format(len(missing), used, speech.audio_root())
        ))

    def _prune(self, notes, dry_run):
        root = speech.audio_root()
        if not root.is_dir():
            self.stdout.write('音声のフォルダがありません。')
            return
        used = {speech.audio_name(n) for n in notes}
        stale = [p for p in root.iterdir() if p.is_file() and p.name not in used]
        for path in stale:
            if not dry_run:
                path.unlink()
        self.stdout.write('使われていない音声: {} 個{}'.format(
            len(stale), '（消していません）' if dry_run else 'を消しました',
        ))
