"""立場（ロール）の判定。

メンバー・リーダーは現場ごと（Membership）、マネジメント・会社は現場をまたぐので
Django のグループで割り当てる。
"""
from django.contrib.auth.models import Group

from accounts.models import CustomUser

from .models import Membership, Site

GROUP_MANAGEMENT = 'genba_management'
GROUP_COMPANY = 'genba_company'
GUEST_USERNAME = 'genba_guest'


def guest_user():
    """デモ：ログインしていない人が共通で使うユーザー。マネジメント・会社の立場も持たせ、
    4つの立場の画面をログインせずに試せるようにする"""
    user, created = CustomUser.objects.get_or_create(
        username=GUEST_USERNAME, defaults={'email': f'{GUEST_USERNAME}@example.com'})
    if created:
        user.set_unusable_password()
        user.save()
        for name in (GROUP_MANAGEMENT, GROUP_COMPANY):
            user.groups.add(Group.objects.get_or_create(name=name)[0])
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
