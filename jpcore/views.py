# -*- coding: utf-8 -*-
import mimetypes
import os
import urllib.error
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import signing
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.urls import reverse
from django.views.generic import TemplateView, View

from .materials_meta import DECKS, DECKS_BY_SLUG
from .sample_data import SAMPLE_ITEMS, SAMPLE_LABEL, SAMPLE_REPO
# 模擬実行は CLI ツール本体（同梱）の関数をそのまま使う＝ツールのデモ。
from .tools import issue_improver as tool

OFFICE_EMBED = "https://view.officeapps.live.com/op/embed.aspx?src="

# PPTX 公開URLの署名設定。トークンは SECRET_KEY で署名され偽造不可。
PPTX_TOKEN_SALT = "jpcore.pptx.public"
# トークンの有効期限（秒）。Office ビューアの取得に十分な余裕を持たせる。
PPTX_URL_MAX_AGE = getattr(settings, "JPCORE_PPTX_URL_MAX_AGE", 3600)

MATERIALS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "materials")


class _DemoError(Exception):
    """模擬実行で利用者に表示する想定のエラー。"""


def _public_pptx_embed(request, deck):
    """Office Online ビューア用の埋め込みURLを生成する。

    Microsoft 側サーバが PPTX を取得するため公開URLが必要だが、URL推測による
    無断アクセスを防ぐため、ログイン済みユーザーのページ表示時に「そのPPTX専用・
    期限つき」の署名トークンを付与する。本番(HTTPS)では https を強制する。
    """
    if not deck.get("pptx"):
        return None
    token = signing.dumps(deck["slug"], salt=PPTX_TOKEN_SALT)
    rel = reverse("jpcore:material_public", args=[deck["slug"]])
    abs_url = request.build_absolute_uri(rel) + "?t=" + quote(token, safe="")
    if not settings.DEBUG and abs_url.startswith("http://"):
        abs_url = "https://" + abs_url[len("http://"):]
    return OFFICE_EMBED + quote(abs_url, safe="")


def _attach_steps(rows):
    """各 Issue 行に、工程概要(steps)と全工程プロンプト(steps_list)を付与する。"""
    for r in rows:
        r["steps"] = tool.STEPS[r["path"]]
        r["steps_list"] = tool.build_steps(r)


def _build_rows(repo, label, milestone, state, order, case=""):
    """CLI ツールの関数で GitHub から Issue を取得し、一覧＋全工程プロンプトを作る。"""
    parsed = tool.parse_repo(repo)
    if not parsed:
        raise _DemoError("リポジトリ指定が不正です（owner/name か GitHub URL を入力してください）。")
    if not label:
        raise _DemoError("SWG ラベルを入力してください。")
    try:
        items = tool.fetch_issues(parsed, label, milestone or None, state)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise _DemoError("GitHub API のレート制限に達した可能性があります。"
                             "少し待つか、サーバに GITHUB_TOKEN を設定してください。")
        raise _DemoError(f"GitHub API エラー: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        raise _DemoError(f"ネットワークエラー: {e.reason}")
    if not items:
        raise _DemoError(f"該当 Issue がありません（repo={parsed} / label={label}）。"
                         "ラベル名は GitHub 表記と完全一致が必要です。")
    rows = tool.order_rows(tool.filter_case(tool.enrich(items), case), order)
    _attach_steps(rows)
    return parsed, rows


class IndexView(LoginRequiredMixin, TemplateView):
    """JP Core Issue 改善支援の紹介ページ（3番目のコンテンツ）。"""
    template_name = "jpcore/index.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["decks"] = DECKS
        return ctx


class MaterialsView(LoginRequiredMixin, TemplateView):
    """資料の閲覧ページ。各デッキを PowerPoint のまま Office Online ビューアで表示する。"""
    template_name = "jpcore/materials.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        slug = self.request.GET.get("deck")
        decks = [d for d in DECKS if d.get("pptx")]
        current = DECKS_BY_SLUG.get(slug)
        if current is None or not current.get("pptx"):
            current = decks[0]
        ctx["decks"] = decks
        ctx["current"] = current
        ctx["office_embed"] = _public_pptx_embed(self.request, current)
        ctx["is_localhost"] = self.request.get_host().split(":")[0] in ("127.0.0.1", "localhost")
        return ctx


class PublicMaterialFileView(View):
    """PPTX を配信する公開エンドポイント（署名付き・期限つきトークン必須）。

    Office Online ビューア(view.officeapps.live.com)は Microsoft 側サーバが
    ファイルを取得して描画するため、ユーザーのログインCookieが届かない。そこで
    ログイン済みユーザーのページ表示時に発行した署名トークン(?t=...)を検証して
    配信する。トークンは SECRET_KEY 署名で偽造不可・期限切れで失効するため、
    URL（slug）を推測しただけの第三者はアクセスできない。
    配信対象は materials_meta 登録の PPTX のみ（ホワイトリスト＋トラバーサル対策）。
    """

    def get(self, request, slug):
        deck = DECKS_BY_SLUG.get(slug)
        if deck is None or not deck.get("pptx"):
            raise Http404("資料が見つかりません。")
        # 署名トークン検証（無い/不正/期限切れ/別slug は拒否）
        token = request.GET.get("t", "")
        try:
            signed_slug = signing.loads(token, salt=PPTX_TOKEN_SALT, max_age=PPTX_URL_MAX_AGE)
        except signing.SignatureExpired:
            return HttpResponseForbidden("リンクの有効期限が切れています。資料ページを開き直してください。")
        except signing.BadSignature:
            return HttpResponseForbidden("不正なリンクです。")
        if signed_slug != slug:
            return HttpResponseForbidden("不正なリンクです。")
        filename = deck["pptx"]
        path = os.path.realpath(os.path.join(MATERIALS_DIR, filename))
        if not path.startswith(os.path.realpath(MATERIALS_DIR) + os.sep) or not os.path.isfile(path):
            raise Http404("ファイルが存在しません。")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        resp = FileResponse(open(path, "rb"), content_type=ctype)
        # ビューアがインラインで読み込めるよう inline 指定
        resp["Content-Disposition"] = f"inline; filename*=UTF-8''{quote(filename)}"
        # Office のフェッチャがアクセスするため、認証や同一オリジン制限を課さない
        return resp


class ToolView(LoginRequiredMixin, TemplateView):
    """issue_improver の模擬実行エリア（読み取り専用）。

    CLI ツール本体（jpcore/tools/issue_improver.py）の関数をそのまま呼び、
    GitHub 公開 API から Issue を取得して 一覧化・ケース分類・spec-kit 全工程
    プロンプト生成 を行い表示する。Claude Code 実行や git 操作は一切行わない。
    """
    template_name = "jpcore/tool.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("form", {
            "repo": "jami-fhir-jp-wg/jp-core-v1x",
            "label": "",
            "milestone": "",
            "state": "open",
            "order": "severity",
            "case": "",
        })
        return ctx

    def post(self, request, *args, **kwargs):
        form = {
            "repo": (request.POST.get("repo") or "").strip(),
            "label": (request.POST.get("label") or "").strip(),
            "milestone": (request.POST.get("milestone") or "").strip(),
            "state": request.POST.get("state") or "open",
            "order": request.POST.get("order") or "severity",
            "case": request.POST.get("case") or "",
        }
        use_sample = request.POST.get("mode") == "sample"
        ctx = self.get_context_data(**kwargs)
        ctx["form"] = form
        ctx["executed"] = True
        ctx["use_sample"] = use_sample
        try:
            if use_sample:
                repo = SAMPLE_REPO
                rows = tool.order_rows(
                    tool.filter_case(tool.enrich(SAMPLE_ITEMS), form["case"]), form["order"])
                _attach_steps(rows)
                ctx["form"]["repo"] = SAMPLE_REPO
                ctx["form"]["label"] = SAMPLE_LABEL
            else:
                repo, rows = _build_rows(
                    form["repo"], form["label"], form["milestone"],
                    form["state"], form["order"], form["case"])
            ctx["repo"] = repo
            ctx["rows"] = rows
            counts = {"A": 0, "B": 0, "C": 0, "D": 0}
            for r in rows:
                counts[r["case"]] = counts.get(r["case"], 0) + 1
            ctx["counts"] = counts
        except _DemoError as e:
            ctx["error"] = str(e)
        except Exception as e:  # noqa: BLE001 想定外も画面で知らせる
            ctx["error"] = f"想定外のエラー: {e}"
        return self.render_to_response(ctx)
