"""立場（ロール）の判定。

メンバー・リーダーは現場ごと（Membership）、マネジメント・会社は現場をまたぐので
Django のグループで割り当てる。
"""
from .models import Membership, Site

GROUP_MANAGEMENT = 'genba_management'
GROUP_COMPANY = 'genba_company'


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
