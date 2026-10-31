"""テストの実行。廃止したアプリのテストを、探索の対象から外す。

ap は廃止して INSTALLED_APPS から外したが、コードはリポジトリに残っている。
テストの探索は INSTALLED_APPS を見ずにフォルダをたどるので、外さないと読み込みエラーになる。
"""
from django.test.runner import DiscoverRunner, iter_test_cases

RETIRED_APPS = ('ap',)


class ProjectTestRunner(DiscoverRunner):
    def build_suite(self, *args, **kwargs):
        suite = super().build_suite(*args, **kwargs)
        # 読み込めなかったテストモジュールは unittest.loader._FailedTest.<モジュール名> という id になる
        prefixes = tuple(f'{app}.' for app in RETIRED_APPS)
        prefixes += tuple(f'unittest.loader._FailedTest.{app}.' for app in RETIRED_APPS)
        return self.test_suite(test for test in iter_test_cases(suite) if not test.id().startswith(prefixes))
