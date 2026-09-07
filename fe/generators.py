"""計算問題のテンプレート。

過去問で毎回のように出るが数値を変えれば何度でも演習できる型
（稼働率，基数変換，伝送時間，損益分岐点 など）をここで作る。
生成した問題は Question として保存し，同じパラメータなら同じ行を
使い回すので，テンプレート由来の問題にも正答率が積み上がる。
"""

import math
import random

from .models import Question, QuestionTemplate

# ---------------------------------------------------------------- 補助


def _fmt(value, digits=None):
    """末尾の 0 を落として数値を読みやすく整形する。"""
    if isinstance(value, int):
        return '{:,}'.format(value)
    if digits is not None:
        value = round(value, digits)
    if abs(value - round(value)) < 1e-9:
        return '{:,}'.format(int(round(value)))
    text = '{:,.4f}'.format(value).rstrip('0').rstrip('.')
    return text


def _unique(correct, candidates, limit=3):
    """正解と重複しない誤答を limit 個そろえる。"""
    result = []
    for candidate in candidates:
        if candidate == correct or candidate in result:
            continue
        result.append(candidate)
        if len(result) == limit:
            break
    return result


# ---------------------------------------------------------------- 各テンプレート


def _build_radix(rng):
    n = rng.randint(100, 250)
    correct = format(n, 'X')
    distractors = _unique(correct, [
        format(n + 1, 'X'), format(n - 1, 'X'), format(n, 'o'),
        format(n + 16, 'X'), format(n // 2, 'X'),
    ])
    return {
        'stem': '10進数 {} を16進数で表したものはどれか。'.format(n),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            '{n} を16で割ると商 {q}，余り {r}。商 {q} は16進数で {qh}，'
            '余り {r} は {rh} なので {ans} になる。'
            '（検算：{qh}×16＋{rh} ＝ {q}×16＋{r} ＝ {n}）'
        ).format(n=n, q=n // 16, r=n % 16, qh=format(n // 16, 'X'),
                 rh=format(n % 16, 'X'), ans=correct),
        'params': {'n': n},
    }


def _build_twos_complement(rng):
    n = rng.randint(3, 120)
    correct = format((256 - n) % 256, '08b')
    distractors = _unique(correct, [
        format(255 - n, '08b'),          # 1の補数どまり
        format(n, '08b'),                # 元の値のまま
        format((256 - n + 1) % 256, '08b'),
        format((n ^ 0xFF) + 2 & 0xFF, '08b'),
    ])
    return {
        'stem': (
            '8ビットの2進数で負数を2の補数で表現する。10進数 {n} を表す'
            '2進数 {b} に対し，－{n} を表すビット列はどれか。'
        ).format(n=n, b=format(n, '08b')),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            '2の補数は「全ビット反転して1を足す」。{b} を反転すると {inv}，'
            '1を足して {ans}。（検算：{b}＋{ans} は8ビットの範囲で 00000000 になる）'
        ).format(b=format(n, '08b'), inv=format(255 - n, '08b'), ans=correct),
        'params': {'n': n},
    }


def _build_bit_mask(rng):
    value = rng.randint(0x21, 0xFE)
    mask, op, label = rng.choice([
        (0x0F, 'and', '論理積（AND）'),
        (0xF0, 'and', '論理積（AND）'),
        (0x0F, 'or', '論理和（OR）'),
        (0xFF, 'xor', '排他的論理和（XOR）'),
    ])
    if op == 'and':
        answer = value & mask
        desc = '対応するビットが両方1のときだけ1になる'
    elif op == 'or':
        answer = value | mask
        desc = 'どちらかのビットが1なら1になる'
    else:
        answer = value ^ mask
        desc = '対応するビットが異なるときだけ1になる'

    correct = format(answer, '08b')
    # 他の演算子で計算した結果（＝取り違えたときに出る値）を誤答にする
    distractors = _unique(correct, [
        format(value & mask, '08b'),
        format(value | mask, '08b'),
        format(value ^ mask, '08b'),
        format(value & ~mask & 0xFF, '08b'),
        format(~value & 0xFF, '08b'),
        format(value, '08b'),
        format(mask, '08b'),
        format(~mask & 0xFF, '08b'),
        format((value + mask) & 0xFF, '08b'),
        format(((value & 0x0F) << 4) | (value >> 4), '08b'),
    ])
    return {
        'stem': (
            '8ビットのビット列 {v} と {m} のビットごとの{label}をとった結果はどれか。'
        ).format(v=format(value, '08b'), m=format(mask, '08b'), label=label),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            '{label}は{desc}演算である。{v} と {m} を1ビットずつ照合すると {ans} になる。'
        ).format(label=label, desc=desc, v=format(value, '08b'),
                 m=format(mask, '08b'), ans=correct),
        'params': {'value': value, 'mask': mask, 'op': op},
    }


def _build_hash(rng):
    modulus = rng.choice([7, 11, 13])
    digits = [rng.randint(1, 9) for _ in range(5)]
    number = int(''.join(str(d) for d in digits))
    total = sum(digits)
    answer = total % modulus
    correct = str(answer)
    # 誤答も 0〜{m-1} の範囲に収める（範囲外の値は消去法で捨てられてしまう）
    distractors = _unique(correct, [
        str(number % modulus),
        str((total + 1) % modulus),
        str((total - 1) % modulus),
        str((total * 2) % modulus),
        str((total + 2) % modulus),
    ])
    return {
        'stem': (
            '10進5桁の数 {n} を，ハッシュ法を用いて配列に格納する。ハッシュ関数を'
            '「各桁の数字の総和を {m} で割った余り」とするとき，{n} が格納される'
            '配列の位置（添字）はどれか。'
        ).format(n=number, m=modulus),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            '各桁の和は {sum_expr} ＝ {total}。{total} を {m} で割ると'
            '商 {q} 余り {r} なので，添字は {r}。'
        ).format(sum_expr='＋'.join(str(d) for d in digits), total=total,
                 m=modulus, q=total // modulus, r=answer),
        'params': {'digits': digits, 'modulus': modulus},
    }


def _build_binary_search(rng):
    exponent = rng.randint(7, 12)
    n = 2 ** exponent - 1
    correct = str(exponent)
    distractors = _unique(correct, [
        str(exponent + 1), str(exponent - 1), str(n // 2), str(exponent * 2),
    ])
    return {
        'stem': (
            '昇順に整列済みの {n} 個のデータから，2分探索法で目的のデータを'
            '探索する。最大で何回の比較が必要か。'
        ).format(n=n),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            '1回比較するごとに候補が半分になる。{n} ＝ 2^{k} － 1 なので，'
            '{n} → {a} → {b} → … と絞り込み，{k} 回で候補が1個になる。'
        ).format(n=n, k=exponent, a=(n - 1) // 2, b=((n - 1) // 2 - 1) // 2),
        'params': {'exponent': exponent},
    }


def _build_availability_serial(rng):
    a = rng.choice([0.8, 0.9, 0.95, 0.98])
    b = rng.choice([0.8, 0.9, 0.95, 0.99])
    serial = a * b
    parallel = 1 - (1 - a) * (1 - b)
    is_serial = rng.random() < 0.5

    if is_serial:
        answer, wording = serial, '直列（2台とも動作していないとシステムが動作しない）'
        formula = '{}×{} ＝ {}'.format(_fmt(a), _fmt(b), _fmt(serial, 4))
    else:
        answer, wording = parallel, '並列（どちらか1台が動作していればシステムが動作する）'
        formula = '1－(1－{})×(1－{}) ＝ {}'.format(_fmt(a), _fmt(b), _fmt(parallel, 4))

    correct = _fmt(answer, 4)
    distractors = _unique(correct, [
        _fmt(parallel if is_serial else serial, 4),
        _fmt(a + b - 1, 4),
        _fmt((a + b) / 2, 4),
        _fmt(min(a, b), 4),
    ])
    return {
        'stem': (
            '稼働率がそれぞれ {a}，{b} である2台の装置を{wording}構成で接続した。'
            'このシステム全体の稼働率はおよそ幾らか。'
        ).format(a=_fmt(a), b=_fmt(b), wording=wording),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            '直列は「両方が動いている確率」なので積，並列は「両方止まる確率」の'
            '余事象で求める。今回は {f}。'
        ).format(f=formula),
        'params': {'a': a, 'b': b, 'serial': is_serial},
    }


def _build_availability_mtbf(rng):
    mtbf, mttr = rng.choice([
        (1500, 100), (2000, 500), (2400, 600), (3000, 1000),
        (4000, 1000), (4500, 500), (900, 100),
    ])
    rate = mtbf / (mtbf + mttr) * 100
    correct = _fmt(rate, 1) + '％'
    distractors = _unique(correct, [
        _fmt(mttr / (mtbf + mttr) * 100, 1) + '％',
        _fmt(mtbf / mttr, 1) + '％',
        _fmt((mtbf - mttr) / mtbf * 100, 1) + '％',
        _fmt(rate - 5, 1) + '％',
    ])
    return {
        'stem': (
            'ある装置の MTBF は {mtbf} 時間，MTTR は {mttr} 時間である。'
            'この装置の稼働率はおよそ何％か。'
        ).format(mtbf=_fmt(mtbf), mttr=_fmt(mttr)),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            '稼働率 ＝ MTBF ÷（MTBF＋MTTR）。{mtbf} ÷（{mtbf}＋{mttr}）'
            '＝ {mtbf} ÷ {total} ＝ {r}。MTBF は平均故障間隔（動いている時間の平均），'
            'MTTR は平均修理時間である。'
        ).format(mtbf=_fmt(mtbf), mttr=_fmt(mttr), total=_fmt(mtbf + mttr),
                 r=correct),
        'params': {'mtbf': mtbf, 'mttr': mttr},
    }


def _build_raid(rng):
    level = rng.choice(['RAID0', 'RAID1', 'RAID5', 'RAID6'])
    disks = rng.choice([4, 5, 6, 8])
    capacity = rng.choice([500, 1000, 2000])
    table = {
        'RAID0': (disks, 'ストライピングだけなので全容量を使える'),
        'RAID1': (disks // 2, 'ミラーリングで同じ内容を2重に持つので半分'),
        'RAID5': (disks - 1, 'パリティを1台分ぶん持つので1台減る'),
        'RAID6': (disks - 2, 'パリティを2台分ぶん持つので2台減る'),
    }
    factor, reason = table[level]
    answer = factor * capacity
    correct = '{} Gバイト'.format(_fmt(answer))
    distractors = _unique(correct, [
        '{} Gバイト'.format(_fmt(disks * capacity)),
        '{} Gバイト'.format(_fmt((disks - 1) * capacity)),
        '{} Gバイト'.format(_fmt(disks // 2 * capacity)),
        '{} Gバイト'.format(_fmt((disks - 2) * capacity)),
        '{} Gバイト'.format(_fmt(capacity)),
    ])
    return {
        'stem': (
            '容量 {c} Gバイトのディスク {d} 台で {lv} を構成した。'
            '利用者が使える実効容量は幾らか。'
        ).format(c=_fmt(capacity), d=disks, lv=level),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            '{lv} は{reason}ため，実効容量は {f} 台分。'
            '{f} × {c} Gバイト ＝ {ans}。'
        ).format(lv=level, reason=reason, f=factor, c=_fmt(capacity), ans=correct),
        'params': {'level': level, 'disks': disks, 'capacity': capacity},
    }


def _build_effective_access(rng):
    cache = rng.choice([10, 15, 20])
    main = rng.choice([60, 80, 100])
    hit = rng.choice([0.8, 0.9, 0.95])
    answer = hit * cache + (1 - hit) * main
    correct = '{} ナノ秒'.format(_fmt(answer, 2))
    distractors = _unique(correct, [
        '{} ナノ秒'.format(_fmt((1 - hit) * cache + hit * main, 2)),
        '{} ナノ秒'.format(_fmt((cache + main) / 2, 2)),
        '{} ナノ秒'.format(_fmt(cache + main, 2)),
        '{} ナノ秒'.format(_fmt(main - cache, 2)),
    ])
    return {
        'stem': (
            'キャッシュメモリのアクセス時間が {c} ナノ秒，主記憶のアクセス時間が'
            ' {m} ナノ秒，キャッシュのヒット率が {h} であるとき，実効アクセス時間は'
            'およそ幾らか。'
        ).format(c=cache, m=main, h=_fmt(hit)),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            '実効アクセス時間 ＝ ヒット率×キャッシュのアクセス時間 ＋ '
            '（1－ヒット率）×主記憶のアクセス時間。'
            '{h}×{c} ＋ {mh}×{m} ＝ {ans}。'
        ).format(h=_fmt(hit), c=cache, mh=_fmt(1 - hit), m=main, ans=correct),
        'params': {'cache': cache, 'main': main, 'hit': hit},
    }


def _build_transfer_time(rng):
    # 結果がきれいな整数になる組合せだけを使う
    speed, efficiency, seconds = rng.choice([
        (1.5, 0.5, 128), (1.5, 0.8, 40), (3.0, 0.5, 64),
        (6.0, 0.8, 50), (10.0, 0.5, 80), (100.0, 0.8, 20),
    ])
    megabytes = speed * efficiency * seconds / 8
    correct = '{} 秒'.format(_fmt(seconds))
    distractors = _unique(correct, [
        '{} 秒'.format(_fmt(seconds / 2)),
        '{} 秒'.format(_fmt(seconds * 2)),
        '{} 秒'.format(_fmt(seconds * efficiency)),
        '{} 秒'.format(_fmt(seconds / 8)),
    ])
    return {
        'stem': (
            '伝送速度 {s} Mビット／秒の回線を用いて {d} Mバイトのデータを転送する。'
            '回線の伝送効率を {e} とするとき，転送に必要な時間は何秒か。'
        ).format(s=_fmt(speed), d=_fmt(megabytes), e=_fmt(efficiency)),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            'まずバイトをビットに直す：{d} Mバイト × 8 ＝ {bits} Mビット。'
            '実際に出る速度は {s} × {e} ＝ {eff} Mビット／秒。'
            '{bits} ÷ {eff} ＝ {ans}。バイトとビットの換算忘れが定番の失点源。'
        ).format(d=_fmt(megabytes), bits=_fmt(megabytes * 8), s=_fmt(speed),
                 e=_fmt(efficiency), eff=_fmt(speed * efficiency), ans=correct),
        'params': {'speed': speed, 'efficiency': efficiency, 'seconds': seconds},
    }


def _build_subnet(rng):
    prefix = rng.choice([25, 26, 27, 28, 29])
    bits = 32 - prefix
    answer = 2 ** bits - 2
    correct = str(answer)
    distractors = _unique(correct, [
        str(2 ** bits), str(2 ** bits - 1), str(2 ** (bits + 1) - 2),
        str(2 ** (bits - 1) - 2),
    ])
    octet = 256 - 2 ** bits
    return {
        'stem': (
            'IPv4 ネットワーク 192.168.10.0/{p}（サブネットマスク 255.255.255.{o}）'
            'において，ホストに割り当てられる IP アドレスは最大幾つか。'
        ).format(p=prefix, o=octet),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            'ホスト部は 32－{p} ＝ {b} ビットで，表せるアドレスは 2^{b} ＝ {all} 個。'
            'このうち全ビット0のネットワークアドレスと全ビット1のブロードキャスト'
            'アドレスは割り当てられないので，{all} － 2 ＝ {ans} 個。'
        ).format(p=prefix, b=bits, all=2 ** bits, ans=answer),
        'params': {'prefix': prefix},
    }


def _build_queue(rng):
    # 利用率 0.5 は「待ち時間＝サービス時間」となり誤答が作りにくいので外す
    utilization, factor = rng.choice([
        (0.2, 0.25), (0.6, 1.5), (0.75, 3.0), (0.8, 4.0), (0.9, 9.0),
    ])
    service = rng.choice([10, 20, 40, 50])
    answer = factor * service
    correct = '{} ミリ秒'.format(_fmt(answer))
    distractors = _unique(correct, [
        '{} ミリ秒'.format(_fmt(answer + service)),      # 応答時間との取り違え
        '{} ミリ秒'.format(_fmt(utilization * service)),
        '{} ミリ秒'.format(_fmt(service)),
        '{} ミリ秒'.format(_fmt(service * (1 - utilization))),
        '{} ミリ秒'.format(_fmt(answer / 2)),
        '{} ミリ秒'.format(_fmt(answer * 2)),
    ])
    return {
        'stem': (
            'M/M/1 の待ち行列モデルが適用できるシステムがある。平均サービス時間が'
            ' {s} ミリ秒，窓口の利用率が {u} であるとき，平均待ち時間はおよそ何ミリ秒か。'
        ).format(s=service, u=_fmt(utilization)),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            'M/M/1 の平均待ち時間 ＝ ρ÷(1－ρ) × 平均サービス時間。'
            '{u}÷(1－{u}) ＝ {f} なので，{f}×{s} ＝ {ans}。'
            '「待ち時間」と「応答時間（待ち時間＋サービス時間）」の取り違えに注意。'
        ).format(u=_fmt(utilization), f=_fmt(factor), s=service, ans=correct),
        'params': {'utilization': utilization, 'service': service},
    }


def _build_sla_downtime(rng):
    availability, days = rng.choice([
        (0.999, 30), (0.995, 30), (0.99, 30), (0.999, 365), (0.9999, 30),
    ])
    hours = days * 24
    downtime_minutes = hours * 60 * (1 - availability)
    correct = '{} 分'.format(_fmt(downtime_minutes, 1))
    distractors = _unique(correct, [
        '{} 分'.format(_fmt(downtime_minutes * 10, 1)),
        '{} 分'.format(_fmt(downtime_minutes / 10, 1)),
        '{} 分'.format(_fmt(hours * (1 - availability), 1)),
        '{} 分'.format(_fmt(downtime_minutes * 2, 1)),
    ])
    return {
        'stem': (
            'SLA で「{d} 日間の稼働率 {a} 以上」と定めたサービスがある。'
            'この期間に許容される停止時間は最大でおよそ何分か。'
        ).format(d=days, a=_fmt(availability)),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            '対象期間は {d} 日 × 24 時間 × 60 分 ＝ {mins} 分。'
            '停止が許されるのはそのうち 1－{a} ＝ {rate} なので，'
            '{mins} × {rate} ＝ {ans}。'
        ).format(d=days, mins=_fmt(hours * 60), a=_fmt(availability),
                 rate=_fmt(1 - availability, 6), ans=correct),
        'params': {'availability': availability, 'days': days},
    }


def _build_break_even(rng):
    ratio = rng.choice([0.4, 0.5, 0.6, 0.75])
    fixed = rng.choice([20, 30, 40, 60, 90])
    sales = rng.choice([100, 200, 400])
    variable = sales * ratio
    answer = fixed / (1 - ratio)
    correct = '{} 百万円'.format(_fmt(answer, 2))
    distractors = _unique(correct, [
        '{} 百万円'.format(_fmt(fixed + variable, 2)),
        '{} 百万円'.format(_fmt(fixed / ratio, 2)),
        '{} 百万円'.format(_fmt(fixed, 2)),
        '{} 百万円'.format(_fmt(sales - fixed, 2)),
    ])
    return {
        'stem': (
            '売上高が {s} 百万円のとき，変動費が {v} 百万円，固定費が {f} 百万円'
            '掛かる。変動費率と固定費が変わらないとき，損益分岐点売上高は何百万円か。'
        ).format(s=_fmt(sales), v=_fmt(variable), f=_fmt(fixed)),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            '変動費率 ＝ {v} ÷ {s} ＝ {r}。限界利益率は 1－{r} ＝ {m}。'
            '損益分岐点売上高 ＝ 固定費 ÷ 限界利益率 ＝ {f} ÷ {m} ＝ {ans}。'
        ).format(v=_fmt(variable), s=_fmt(sales), r=_fmt(ratio),
                 m=_fmt(1 - ratio), f=_fmt(fixed), ans=correct),
        'params': {'ratio': ratio, 'fixed': fixed, 'sales': sales},
    }


def _build_depreciation(rng):
    price = rng.choice([30, 40, 60, 80, 120])
    years = rng.choice([4, 5, 6])
    elapsed = rng.randint(2, years - 1)
    sale = rng.choice([1, 2, 3, 5])
    annual = price / years
    book_value = price - annual * elapsed
    answer = book_value - sale
    correct = '{} 万円'.format(_fmt(answer, 2))
    distractors = _unique(correct, [
        '{} 万円'.format(_fmt(book_value, 2)),
        '{} 万円'.format(_fmt(price - sale, 2)),
        '{} 万円'.format(_fmt(annual * elapsed, 2)),
        '{} 万円'.format(_fmt(price - annual * (elapsed + 1) - sale, 2)),
    ])
    return {
        'stem': (
            '{p} 万円で購入した PC を {e} 年後に {s} 万円で売却する。'
            '耐用年数 {y} 年，減価償却は定額法（償却率 {rate}），残存価額は0円'
            'とするとき，固定資産売却損は何万円か。'
        ).format(p=_fmt(price), e=elapsed, s=_fmt(sale), y=years,
                 rate=_fmt(1 / years, 3)),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            '年間の償却費は {p} ÷ {y} ＝ {a} 万円。{e} 年間で {total} 万円を'
            '償却するので，売却時の帳簿価額は {p} － {total} ＝ {bv} 万円。'
            '売却額 {s} 万円との差 {bv} － {s} ＝ {ans} が売却損。'
        ).format(p=_fmt(price), y=years, a=_fmt(annual, 2), e=elapsed,
                 total=_fmt(annual * elapsed, 2), bv=_fmt(book_value, 2),
                 s=_fmt(sale), ans=correct),
        'params': {'price': price, 'years': years, 'elapsed': elapsed, 'sale': sale},
    }


def _build_payback(rng):
    investment = rng.choice([600, 800, 1200, 1500, 2000])
    annual = rng.choice([100, 150, 200, 250, 300])
    answer = investment / annual
    correct = '{} 年'.format(_fmt(answer, 2))
    distractors = _unique(correct, [
        '{} 年'.format(_fmt(annual / investment * 10, 2)),
        '{} 年'.format(_fmt(answer + 1, 2)),
        '{} 年'.format(_fmt(answer / 2, 2)),
        '{} 年'.format(_fmt(investment / (annual * 2), 2)),
    ])
    return {
        'stem': (
            'あるシステムへの投資額は {i} 万円で，導入後は毎年 {a} 万円の'
            'コスト削減効果が見込まれる。単純回収期間法（PBP）で評価したとき，'
            '投資を回収できるのは何年後か。'
        ).format(i=_fmt(investment), a=_fmt(annual)),
        'correct': correct,
        'distractors': distractors,
        'explanation': (
            '単純回収期間 ＝ 投資額 ÷ 年間の効果額 ＝ {i} ÷ {a} ＝ {ans}。'
            '貨幣の時間価値を考慮しないのが単純回収期間法で，'
            '考慮するのが DCF 法（正味現在価値法・内部収益率法）。'
        ).format(i=_fmt(investment), a=_fmt(annual), ans=correct),
        'params': {'investment': investment, 'annual': annual},
    }


# ---------------------------------------------------------------- レジストリ

# (テンプレートキー, 題材, 中分類番号, 小分類, 生成関数)
TEMPLATE_SPECS = [
    ('calc-radix', '基数変換', 1, '離散数学', _build_radix),
    ('calc-twos-complement', '2の補数', 1, '離散数学', _build_twos_complement),
    ('calc-queue', '待ち行列（M/M/1）', 1, '応用数学', _build_queue),
    ('calc-bit-mask', 'ビット演算', 2, 'プログラミング', _build_bit_mask),
    ('calc-hash', 'ハッシュ法の格納位置', 2, 'アルゴリズム', _build_hash),
    ('calc-binary-search', '2分探索の比較回数', 2, 'アルゴリズム', _build_binary_search),
    ('calc-effective-access', '実効アクセス時間', 3, 'メモリ', _build_effective_access),
    ('calc-availability-serial', '直列・並列の稼働率', 4, 'システムの評価指標',
     _build_availability_serial),
    ('calc-availability-mtbf', 'MTBF・MTTR と稼働率', 4, 'システムの評価指標',
     _build_availability_mtbf),
    ('calc-raid', 'RAID の実効容量', 4, 'システムの構成', _build_raid),
    ('calc-transfer-time', '伝送時間と伝送効率', 10, 'ネットワーク方式', _build_transfer_time),
    ('calc-subnet', 'サブネットとホスト数', 10, 'ネットワーク方式', _build_subnet),
    ('calc-sla-downtime', 'SLA と許容停止時間', 15, 'サービスマネジメント',
     _build_sla_downtime),
    ('calc-break-even', '損益分岐点売上高', 22, '会計・財務', _build_break_even),
    ('calc-depreciation', '減価償却と売却損', 22, '会計・財務', _build_depreciation),
    ('calc-payback', '投資の回収期間（PBP）', 18, 'システム化計画', _build_payback),
]

REGISTRY = {spec[0]: spec for spec in TEMPLATE_SPECS}


def generators_for_category(category):
    """その中分類で使える有効なテンプレートを返す。"""
    return [
        template
        for template in QuestionTemplate.objects.filter(
            category=category, is_active=True
        )
        if template.key in REGISTRY
    ]


def generate_question(template, user, rng=None):
    """テンプレートから Question を1件作る。

    問題はアカウントごとに持つので、生成した問題も利用者のものになる。
    同じ人が同じ数値を引いたときは、同じ問題を使い回す。
    """
    spec = REGISTRY.get(template.key)
    if spec is None:
        return None

    rng = rng or random
    built = spec[4](rng)

    distractors = built['distractors']
    if len(distractors) < 3:
        return None

    options = [built['correct']] + distractors[:3]
    rng.shuffle(options)
    answer_index = options.index(built['correct'])

    question, created = Question.objects.get_or_create(
        owner=user, template=template, params=built['params'],
        defaults={
            'subject': Question.SUBJECT_A,
            'category': template.category,
            'topic': template.topic,
            'stem': built['stem'],
            'choices': options,
            'answer_index': answer_index,
            'explanation': built['explanation'],
            'difficulty': 2,
            'source': 'テンプレート「{}」による自動生成'.format(template.title),
        },
    )
    return question


def sync_templates():
    """レジストリの内容を QuestionTemplate に反映する。"""
    from .models import Category

    created = 0
    for key, title, category_code, topic, _ in TEMPLATE_SPECS:
        category = Category.objects.get(code=category_code)
        _, made = QuestionTemplate.objects.update_or_create(
            key=key,
            defaults={
                'title': title,
                'category': category,
                'topic': topic,
                'is_active': True,
            },
        )
        created += 1 if made else 0
    return created
