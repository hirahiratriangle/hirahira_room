"""立場（ロール）の判定。

メンバー・リーダーは現場ごと（Membership）、マネジメント・会社は現場をまたぐので
Django のグループで割り当てる。
"""
from django.contrib.auth.models import Group

from accounts.models import CustomUser

from . import kit
from .models import Membership, Site

GROUP_MANAGEMENT = 'genba_management'
GROUP_COMPANY = 'genba_company'
# 立場の切り替え（デモ）：どの立場を選んでいるかをセッションのこのキーに持つ
PERSONA_SESSION_KEY = 'genba_persona'
# ペルソナのうち、Django のグループで立場を持つもの（メンバー・リーダーは現場の Membership で持つ）
PERSONA_GROUPS = {'management': GROUP_MANAGEMENT, 'company': GROUP_COMPANY}
PERSONAS = {p['key']: p for p in kit.DEMO_PERSONAS}


def persona_user(key):
    """その立場の人（デモのユーザー）を返す。なければ作る。パスワードは使えないので、ログインはできない"""
    username = PERSONAS[key]['username']
    user, created = CustomUser.objects.get_or_create(username=username, defaults={'email': f'{username}@example.com'})
    if created:
        user.set_unusable_password()
        user.save()
        if key in PERSONA_GROUPS:
            user.groups.add(Group.objects.get_or_create(name=PERSONA_GROUPS[key])[0])
    return user


def member_sites(user):
    """原文を読める現場（メンバーまたはリーダーとして入っている現場）"""
    return Site.objects.filter(memberships__user=user).distinct()


def leader_sites(user):
    return Site.objects.filter(memberships__user=user, memberships__role=Membership.ROLE_LEADER).distinct()


def can_read_site(user, site):
    return site.memberships.filter(user=user).exists()


def is_leader(user, site):
    return site.memberships.filter(user=user, role=Membership.ROLE_LEADER).exists()


def in_group(user, name):
    return user.is_authenticated and user.groups.filter(name=name).exists()


def is_management(user):
    return in_group(user, GROUP_MANAGEMENT)


def is_company(user):
    return in_group(user, GROUP_COMPANY)


def can_receive_concern(user, question):
    """懸念を受け取れる人：書いた人がリーダーなら同じ現場の他のメンバー、そうでなければリーダー"""
    if question.author_id == user.pk or not can_read_site(user, question.site):
        return False
    if is_leader(question.author, question.site):
        return True
    return is_leader(user, question.site)


def available_roles(user):
    """ナビに出す立場の一覧（持っている立場だけ）"""
    roles = []
    if member_sites(user).exists():
        roles.append('member')
    if leader_sites(user).exists():
        roles.append('leader')
    if is_management(user):
        roles.append('management')
    if is_company(user):
        roles.append('company')
    return roles
