"""历史记录的容量情景比较。

只读队列、收款、备餐与排班记录，把候选方案（加开窗口、提前备餐等）放在
同一批历史场次上回放比较。比较本身不占用真实班次或食材，只写入评估档案
（evaluation 业务记录）供事后追溯；采纳仍由 scenarios.adopt 按当时记录
重新核验。方法约定见 docs/容量比较.md。
"""
import collections
import heapq
import random
from datetime import datetime, timedelta, timezone
from .store import require

DEFAULTS = dict(
    window_minutes=15,          # 中场服务窗口
    service_horizon_minutes=45, # 取号后服务可延续的时长
    replications=30,            # 每场次重复回放次数
    min_service_sample=8,       # 完整服务时间样本下限
    unknown_share_limit=0.25,   # 未知取号时刻比例上限
    min_matches=3,              # 可用历史场次下限
    validation_unserved_limit=0.2,  # 基线回放未服务比例误差超过该值则不推荐
    prep_service_gain_seconds=0,  # 提前备餐对单笔服务的加速（未验证假设）
    serving_cost_cents=0,       # 每份剩余备餐成本（未验证假设）
    seed=117209,
)

SHIFT_FIELDS = ('worker', 'window', 'skill', 'start', 'end')
BATCH_FIELDS = ('ingredient', 'units', 'servings', 'at')


def parse(stamp):
    return datetime.fromisoformat(stamp)


def seconds_of_day(moment):
    return moment.hour * 3600 + moment.minute * 60 + moment.second


def percentile(values, ratio):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * ratio
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def mean(values):
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None


def kaplan_meier(points):
    """(时长, 是否事件) 序列的生存曲线；删失保留为暴露，不当作零或完整。"""
    events = collections.Counter()
    censored = collections.Counter()
    for moment, is_event in points:
        (events if is_event else censored)[moment] += 1
    remaining = len(points)
    survival = 1.0
    curve = [(0.0, 1.0)]
    for moment in sorted(set(events) | set(censored)):
        gone = events.get(moment, 0)
        lost = censored.get(moment, 0)
        if remaining and gone:
            survival *= (remaining - gone) / remaining
        remaining -= gone + lost
        curve.append((moment, survival))
    return curve


def curve_median(curve):
    for moment, survival in curve:
        if survival <= 0.5:
            return moment
    return None


def curve_sample(curve, rng, cap):
    draw = rng.random()
    for moment, survival in curve:
        if survival <= draw:
            return moment
    return cap


def observe(tickets, start, config):
    """单场观察事实：区分仍在队中、未知时刻与真正无等待，不补写未观测事实。"""
    horizon = config['service_horizon_minutes'] * 60.0
    served = [t for t in tickets if t['state'] == 'served']
    abandoned = [t for t in tickets if t['state'] == 'abandoned']
    waiting = [t for t in tickets if t['state'] == 'waiting']
    called_open = [t for t in tickets if t['state'] == 'called']
    unknown = [t for t in tickets if t['at'] is None]
    no_wait = [t for t in served if t['at'] is not None and t['called_at'] == t['at']]
    wait_points = []
    for ticket in tickets:
        if ticket['at'] is None:
            continue
        at = parse(ticket['at'])
        if ticket['state'] in ('served', 'called'):
            wait_points.append(((parse(ticket['called_at']) - at).total_seconds(), True))
        elif ticket['state'] == 'abandoned':
            wait_points.append(((parse(ticket['abandoned_at']) - at).total_seconds(), False))
        else:
            censor = (start + timedelta(seconds=horizon) - at).total_seconds()
            if censor > 0:
                wait_points.append((censor, False))
    curve = kaplan_meier(wait_points) if wait_points else []
    horizon_end = (start + timedelta(seconds=horizon)).isoformat()
    instants = []
    for ticket in tickets:
        if ticket['state'] == 'served':
            instants.append((ticket['called_at'], 1))
            instants.append((ticket['completed_at'], -1))
        elif ticket['state'] == 'called':
            instants.append((ticket['called_at'], 1))
            instants.append((horizon_end, -1))
    instants.sort()
    concurrent = peak_concurrent = 0
    for _, delta in instants:
        concurrent += delta
        peak_concurrent = max(peak_concurrent, concurrent)
    total = len(tickets)
    unserved = len(abandoned) + len(waiting) + len(called_open)
    return {
        'total': total,
        'served': len(served),
        'abandoned': len(abandoned),
        'waiting': len(waiting),
        'called_open': len(called_open),
        'unknown_time': len(unknown),
        'no_wait': len(no_wait),
        'unserved_share': unserved / total if total else 0.0,
        'gross_cents': sum(t['cents'] for t in served),
        'wait_median_s': curve_median(curve) if curve else None,
        'service_samples': sum(1 for t in served if t.get('called_at') and t.get('completed_at')),
        'max_concurrent_service': peak_concurrent,
    }


def prepare_matches(store, config):
    """按比赛日还原场次：到达、窗口、备餐与观察边界。窗口同时面对整场人流。"""
    grouped = {}
    for ticket in store.all('queue_ticket'):
        stamp = ticket['at'] or ticket['recorded_at']
        grouped.setdefault(stamp[:10], []).append(ticket)
    prepared = collections.defaultdict(lambda: [0, 0])
    for batch in store.all('prepared'):
        slot = prepared[batch['at'][:10]]
        slot[0] += batch['servings']
        slot[1] += batch['remaining']
    horizon = config['service_horizon_minutes'] * 60.0
    matches = []
    for day in sorted(grouped):
        tickets = grouped[day]
        known = [t for t in tickets if t['at'] is not None]
        if config.get('window_start'):
            start = parse(f"{day}T{config['window_start']}:00")
        elif known:
            start = min(parse(t['at']) for t in known)
        else:
            start = min(parse(t['recorded_at']) for t in tickets)
        arrivals = []
        for ticket in tickets:
            stamp = ticket['at'] or ticket['recorded_at']
            offset = (parse(stamp) - start).total_seconds()
            if offset > horizon:
                continue
            arrivals.append((max(0.0, offset), ticket['party']))
        window_counts = {}
        for ticket in tickets:
            slot = window_counts.setdefault(ticket['window'], {'tickets': 0, 'served': 0})
            slot['tickets'] += 1
            if ticket['state'] == 'served':
                slot['served'] += 1
        serving = sorted({t['window'] for t in tickets if t['state'] in ('called', 'served')})
        if not serving:
            serving = sorted({t['window'] for t in tickets})
        servings, remaining = prepared.get(day, [0, 0])
        observed = observe(tickets, start, config)
        matches.append({
            'date': day,
            'start': start,
            'tickets': tickets,
            'arrivals': arrivals,
            'observed': observed,
            'window_counts': window_counts,
            'serving_windows': serving,
            'prepared_servings': servings,
            'remaining_servings': remaining,
            'unknown_share': observed['unknown_time'] / observed['total'] if observed['total'] else 0.0,
            'service_samples': observed['service_samples'],
        })
    return matches


def pools(matches, config):
    """跨场次合并的服务时间、收款与耐心分布；未知时刻票据不进入等待与耐心样本。"""
    horizon = config['service_horizon_minutes'] * 60.0
    service = []
    cents = []
    patience_points = []
    for match in matches:
        for ticket in match['tickets']:
            if ticket['state'] == 'served':
                service.append((parse(ticket['completed_at']) - parse(ticket['called_at'])).total_seconds())
                cents.append(ticket['cents'])
            if ticket['at'] is None:
                continue
            at = parse(ticket['at'])
            if ticket['state'] in ('served', 'called'):
                patience_points.append(((parse(ticket['called_at']) - at).total_seconds(), False))
            elif ticket['state'] == 'abandoned':
                patience_points.append(((parse(ticket['abandoned_at']) - at).total_seconds(), True))
            elif ticket['state'] == 'waiting':
                censor = (match['start'] + timedelta(seconds=horizon) - at).total_seconds()
                if censor > 0:
                    patience_points.append((censor, False))
    curve = kaplan_meier(patience_points) if patience_points else []
    return service, cents, curve


def check_plan(plan, constraints, workers, ingredients, shifts):
    """按当前记录核对约束：不可移动、一人一班、技能匹配、食材可用。"""
    violations = []
    immovable = set(constraints.get('immovable', []))
    window_skill = constraints.get('window_skill', {})
    seen = []
    for index, shift in enumerate(plan.get('shifts', [])):
        label = f'班次[{index}]'
        missing = [key for key in SHIFT_FIELDS if shift.get(key) is None]
        if missing:
            violations.append(f"{label} 缺少字段: {','.join(missing)}")
            continue
        worker = workers.get(shift['worker'])
        if worker is None:
            violations.append(f"{label} 员工不存在: {shift['worker']}")
            continue
        if shift['worker'] in immovable:
            violations.append(f"{label} 员工 {shift['worker']} 被指定为不可移动")
        if worker['absent']:
            violations.append(f"{label} 员工 {shift['worker']} 已请假")
        if shift['skill'] not in worker['skills']:
            violations.append(f"{label} 员工 {shift['worker']} 不具备技能 {shift['skill']}")
        required = window_skill.get(shift['window'])
        if required is not None and required != shift['skill']:
            violations.append(f"{label} 窗口 {shift['window']} 需要技能 {required}")
        if not shift['start'] < shift['end']:
            violations.append(f'{label} 起止时刻无效')
        for other in shifts:
            if other['worker'] == shift['worker'] and shift['start'] < other['end'] and shift['end'] > other['start']:
                violations.append(f"{label} 与现有班次 {other['id']} 冲突")
        for prev_start, prev_end, prev_worker in seen:
            if prev_worker == shift['worker'] and shift['start'] < prev_end and shift['end'] > prev_start:
                violations.append(f'{label} 与本方案另一班次冲突（一人不能同时上两个窗口）')
        seen.append((shift['start'], shift['end'], shift['worker']))
    needed = collections.Counter()
    for index, batch in enumerate(plan.get('preparation', [])):
        label = f'备餐[{index}]'
        missing = [key for key in BATCH_FIELDS if batch.get(key) is None]
        if missing:
            violations.append(f"{label} 缺少字段: {','.join(missing)}")
            continue
        ingredient = ingredients.get(batch['ingredient'])
        if ingredient is None:
            violations.append(f"{label} 食材不存在: {batch['ingredient']}")
            continue
        if batch['units'] <= 0 or batch['servings'] <= 0:
            violations.append(f'{label} 备餐量无效')
        needed[batch['ingredient']] += batch['units']
    for name, units in needed.items():
        available = ingredients[name]['units']
        if units > available:
            violations.append(f'食材 {name} 不足: 需要 {units}，现有 {available}')
    return violations


def server_intervals(plan, match, config):
    """基线窗口按该场实际叫号窗口计；方案班次按当日时刻映射到历史场次。"""
    horizon = config['service_horizon_minutes'] * 60.0
    intervals = [(0.0, horizon) for _ in match['serving_windows']]
    base = seconds_of_day(match['start'])
    for shift in plan['shifts']:
        begin = seconds_of_day(parse(shift['start'])) - base
        end = seconds_of_day(parse(shift['end'])) - base
        low, high = max(0.0, float(begin)), min(horizon, float(end))
        if high > low:
            intervals.append((low, high))
    return intervals


def draw_attributes(match, service_pool, cents_pool, patience_curve, config, rng):
    cap = config['service_horizon_minutes'] * 600.0
    party_patience = {}
    attributes = []
    for offset, party in match['arrivals']:
        if party not in party_patience:
            party_patience[party] = curve_sample(patience_curve, rng, cap) if patience_curve else cap
        attributes.append((offset, rng.choice(service_pool), party_patience[party], rng.choice(cents_pool)))
    attributes.sort(key=lambda item: item[0])
    return attributes


def run_simulation(attributes, intervals, stock, gain, window_end):
    """单场单次回放：共享队列、耐心离队、备餐存量约束。到达为历史事实，不重抽。"""
    waiting = collections.deque()
    arrivals = collections.deque(attributes)
    free = [(start, end) for start, end in intervals]
    heapq.heapify(free)
    capacity = sum(max(0.0, end - start) for start, end in intervals)
    waits = []
    gross = 0
    served = 0
    within = 0
    abandoned = 0
    busy = 0.0
    stockout = False
    stock_left = stock
    while free:
        avail, end = heapq.heappop(free)
        while arrivals and arrivals[0][0] <= avail:
            waiting.append(arrivals.popleft())
        if not waiting and arrivals:
            avail = max(avail, arrivals[0][0])
            while arrivals and arrivals[0][0] <= avail:
                waiting.append(arrivals.popleft())
        if waiting:
            kept = [item for item in waiting if item[0] + item[2] > avail]
            abandoned += len(waiting) - len(kept)
            waiting = collections.deque(kept)
        if not waiting:
            if arrivals:
                heapq.heappush(free, (avail, end))
            continue
        if avail >= end:
            continue
        if stock_left <= 0:
            stockout = True
            break
        arrive, service, patience, cents = waiting.popleft()
        actual = max(1.0, service - gain)
        finish = avail + actual
        waits.append(avail - arrive)
        gross += cents
        served += 1
        within += 1 if finish <= window_end else 0
        busy += min(finish, end) - avail
        stock_left -= 1
        if finish < end:
            heapq.heappush(free, (finish, end))
    unserved = abandoned + len(waiting) + len(arrivals)
    total = served + unserved
    return {
        'waits': waits,
        'served': served,
        'unserved_share': unserved / total if total else 0.0,
        'gross_cents': gross,
        'leftover_servings': stock_left,
        'within_window_share': within / served if served else None,
        'utilization': busy / capacity if capacity else None,
        'stockout': stockout,
    }


def aggregate(outcomes):
    wait_medians = [percentile(o['waits'], 0.5) for o in outcomes if o['waits']]
    wait_p90 = [percentile(o['waits'], 0.9) for o in outcomes if o['waits']]
    within = mean(o['within_window_share'] for o in outcomes)
    utilization = mean(o['utilization'] for o in outcomes)
    return {
        'wait_median_s': round(mean(wait_medians), 1) if wait_medians else None,
        'wait_p90_s': round(mean(wait_p90), 1) if wait_p90 else None,
        'unserved_share': round(mean(o['unserved_share'] for o in outcomes), 3),
        'within_window_share': round(within, 3) if within is not None else None,
        'gross_cents': round(mean(o['gross_cents'] for o in outcomes)),
        'leftover_servings': round(mean(o['leftover_servings'] for o in outcomes), 1),
        'utilization': round(utilization, 3) if utilization is not None else None,
        'stockout_share': round(mean(1.0 if o['stockout'] else 0.0 for o in outcomes), 3),
    }


def summarize(entries):
    keys = ('wait_median_s', 'wait_p90_s', 'unserved_share', 'within_window_share',
            'gross_cents', 'leftover_servings', 'utilization', 'stockout_share')
    summary = {}
    for key in keys:
        values = [metrics[key] for _, metrics in entries if metrics[key] is not None]
        summary[key] = {
            'median': round(percentile(values, 0.5), 2) if values else None,
            'p10': round(percentile(values, 0.1), 2) if values else None,
            'p90': round(percentile(values, 0.9), 2) if values else None,
        }
    return summary


def increments(entries, baseline_entries, serving_cost):
    base_by_date = {match['date']: metrics for match, metrics in baseline_entries}
    rows = []
    for match, metrics in entries:
        base = base_by_date[match['date']]
        gross = metrics['gross_cents'] - base['gross_cents']
        leftover = metrics['leftover_servings'] - base['leftover_servings']
        rows.append({
            'date': match['date'],
            'gross_cents': round(gross, 1),
            'leftover_servings': round(leftover, 1),
            'net_cents': round(gross - leftover * serving_cost, 1),
        })
    return rows


def validate(baseline_entries):
    rows = []
    unserved_errors = []
    gross_errors = []
    wait_errors = []
    for match, metrics in baseline_entries:
        observed = match['observed']
        row = {
            'date': match['date'],
            'unserved_observed': round(observed['unserved_share'], 3),
            'unserved_simulated': metrics['unserved_share'],
            'gross_observed': observed['gross_cents'],
            'gross_simulated': metrics['gross_cents'],
        }
        unserved_errors.append(abs(metrics['unserved_share'] - observed['unserved_share']))
        gross_errors.append(abs(metrics['gross_cents'] - observed['gross_cents']))
        if observed['wait_median_s'] is not None and metrics['wait_median_s'] is not None:
            row['wait_median_observed'] = observed['wait_median_s']
            row['wait_median_simulated'] = metrics['wait_median_s']
            wait_errors.append(abs(metrics['wait_median_s'] - observed['wait_median_s']))
        rows.append(row)
    return {
        'per_match': rows,
        'mean_abs_error': {
            'unserved_share': round(mean(unserved_errors), 3) if unserved_errors else None,
            'gross_cents': round(mean(gross_errors), 1) if gross_errors else None,
            'wait_median_s': round(mean(wait_errors), 1) if wait_errors else None,
        },
    }


def bottleneck(entries, config):
    counts = collections.Counter()
    detail = []
    for match, metrics in entries:
        if metrics['stockout_share'] >= 0.3:
            kind = '备餐存量'
        elif metrics['utilization'] is not None and metrics['utilization'] >= 0.85:
            kind = '窗口人手'
        elif match['unknown_share'] > config['unknown_share_limit']:
            kind = '需求不确定'
        else:
            kind = '未见单一瓶颈'
        counts[kind] += 1
        detail.append({'date': match['date'], 'bottleneck': kind})
    return {'counts': dict(counts), 'per_match': detail}


def assumption_notes(config):
    return [
        '未知时刻的取号按登记时刻计入需求；登记时刻不等于实际取号时刻',
        '同一历史场次的各窗口按共享队列整场回放，不逐窗截取最佳时段拼接',
        '服务时间、耐心与收款分布跨场次合并，并假设场次之间稳定',
        '事后补记票据的叫号—完成时长仍计入服务时间样本，但其等待时长不可知',
        '同行观众共享同一耐心阈值，同时离队',
        f"提前备餐对单笔服务时间的加速取 {config['prep_service_gain_seconds']} 秒（未验证假设）",
        f"每份剩余备餐的成本取 {config['serving_cost_cents']} 分（未验证假设）",
        f"服务可持续到取号后 {config['service_horizon_minutes']} 分钟",
        '基线窗口数按该场实际发生叫号的窗口计',
        '方案班次按当日时刻映射到各历史场次，仅比较时段结构',
        '可行性按当前记录核对；采纳时按当时记录重新核验',
    ]


class Analysis:
    def __init__(self, store):
        self.store = store

    def coverage(self, window_minutes=15, min_service_sample=5, unknown_share_limit=0.25):
        config = dict(DEFAULTS, window_minutes=window_minutes)
        matches = prepare_matches(self.store, config)
        require(matches, '没有可检验的历史场次')
        report = []
        for match in matches:
            observed = match['observed']
            flags = []
            if match['unknown_share'] > unknown_share_limit:
                flags.append('未知取号时刻比例偏高，等待分布结论受限')
            if match['service_samples'] < min_service_sample:
                flags.append('完整服务时间样本不足')
            if not match['serving_windows']:
                flags.append('未观察到叫号窗口')
            if observed['max_concurrent_service'] > 2 * max(1, len(match['serving_windows'])):
                flags.append('同时处于服务中的票据数远超窗口数，窗口数不代表产能上限')
            report.append({
                'date': match['date'],
                'tickets': observed['total'],
                'served': observed['served'],
                'abandoned': observed['abandoned'],
                'still_waiting': observed['waiting'],
                'called_open': observed['called_open'],
                'unknown_time': observed['unknown_time'],
                'no_wait': observed['no_wait'],
                'unserved_share': round(observed['unserved_share'], 3),
                'wait_median_s': observed['wait_median_s'],
                'gross_cents': observed['gross_cents'],
                'leftover_servings': match['remaining_servings'],
                'windows': match['window_counts'],
                'service_samples': match['service_samples'],
                'max_concurrent_service': observed['max_concurrent_service'],
                'flags': flags,
            })
        totals = {
            'matches': len(report),
            'tickets': sum(row['tickets'] for row in report),
            'served': sum(row['served'] for row in report),
            'abandoned': sum(row['abandoned'] for row in report),
            'still_waiting': sum(row['still_waiting'] for row in report),
            'unknown_time': sum(row['unknown_time'] for row in report),
            'no_wait': sum(row['no_wait'] for row in report),
            'flagged_matches': sum(1 for row in report if row['flags']),
        }
        return {
            'matches': report,
            'totals': totals,
            'note': '仍在队中与事后补记（未知取号时刻）分别计数；等待中位数按删失保留估计，不把补记时刻当作取号时刻。',
        }

    def evaluate(self, id, plans, constraints=None, assumptions=None, matches=None):
        config = dict(DEFAULTS)
        config.update(assumptions or {})
        constraints = constraints or {}
        require(isinstance(plans, list) and bool(plans), '比较方案为空')
        candidates = [dict(plan, shifts=plan.get('shifts', []), preparation=plan.get('preparation', []))
                      for plan in plans]
        require(all(plan.get('name') for plan in candidates), '方案缺少名称')
        names = ['现状'] + [plan['name'] for plan in candidates]
        require(len(set(names)) == len(names), '方案名称重复')
        candidates = [{'name': '现状', 'shifts': [], 'preparation': []}] + candidates

        workers = {w['id']: w for w in self.store.all('worker')}
        ingredients = {i['id']: i for i in self.store.all('ingredient')}
        shifts = self.store.all('shift')
        feasibility = {plan['name']: check_plan(plan, constraints, workers, ingredients, shifts)
                       for plan in candidates}

        pool_matches = prepare_matches(self.store, config)
        if matches is not None:
            wanted = set(matches)
            pool_matches = [m for m in pool_matches if m['date'] in wanted]
        require(pool_matches, '没有可检验的历史场次')

        service_pool, cents_pool, patience_curve = pools(pool_matches, config)
        insufficient = []
        if len(service_pool) < config['min_service_sample']:
            insufficient.append(f"完整服务时间样本 {len(service_pool)} 份，低于下限 {config['min_service_sample']}")
        steady = [m for m in pool_matches if m['unknown_share'] <= config['unknown_share_limit']]
        if len(steady) < config['min_matches']:
            insufficient.append(f"未知取号比例可控的场次 {len(steady)} 场，低于下限 {config['min_matches']}")
        if not cents_pool:
            insufficient.append('没有已收款记录，无法估计收益')

        simulatable = bool(service_pool) and bool(cents_pool)
        per_match = {plan['name']: [] for plan in candidates}
        if simulatable:
            window_end = config['window_minutes'] * 60.0
            runnable = [plan for plan in candidates if not feasibility[plan['name']]]
            for match in pool_matches:
                replications = []
                for round_ in range(config['replications']):
                    rng = random.Random(f"{config['seed']}:{match['date']}:{round_}")
                    replications.append(draw_attributes(match, service_pool, cents_pool,
                                                        patience_curve, config, rng))
                for plan in runnable:
                    intervals = server_intervals(plan, match, config)
                    stock = match['prepared_servings'] + sum(
                        batch.get('servings') or 0 for batch in plan['preparation'])
                    gain = config['prep_service_gain_seconds'] if plan['preparation'] else 0
                    outcomes = [run_simulation(attrs, intervals, stock, gain, window_end)
                                for attrs in replications]
                    per_match[plan['name']].append((match, aggregate(outcomes)))

        validation = validate(per_match['现状']) if simulatable and per_match['现状'] else None

        plans_section = {}
        for plan in candidates:
            name = plan['name']
            violations = feasibility[name]
            section = {'feasible': not violations, 'violations': violations}
            entries = per_match.get(name) or []
            if not violations and entries:
                section['per_match'] = [
                    dict({'date': match['date'],
                          'demand': match['observed']['total'],
                          'unknown_time': match['observed']['unknown_time']}, **metrics)
                    for match, metrics in entries]
                section['summary'] = summarize(entries)
                section['bottleneck'] = bottleneck(entries, config)
                if name != '现状':
                    rows = increments(entries, per_match['现状'], config['serving_cost_cents'])
                    nets = [row['net_cents'] for row in rows]
                    section['increment'] = {
                        'net_cents': {
                            'median': round(percentile(nets, 0.5), 1),
                            'p10': round(percentile(nets, 0.1), 1),
                            'p90': round(percentile(nets, 0.9), 1),
                        },
                        'per_match': rows,
                    }
            else:
                section['per_match'] = None
            plans_section[name] = section

        blocked = list(insufficient)
        if not simulatable:
            blocked.append('缺少可回放的服务或收款样本，未进行模拟')
        if validation:
            unserved_error = validation['mean_abs_error']['unserved_share']
            if unserved_error is not None and unserved_error > config['validation_unserved_limit']:
                blocked.append(f"基线回放未服务比例平均误差 {unserved_error} 超过 "
                               f"{config['validation_unserved_limit']}，容量模型未通过历史检验")
        recommendation = None
        if not blocked:
            runnable_named = [plan['name'] for plan in candidates[1:] if not feasibility[plan['name']]]
            if not runnable_named:
                blocked.append('没有通过约束核对的备选方案')
            else:
                scored = []
                for name in runnable_named:
                    nets = [row['net_cents'] for row in plans_section[name]['increment']['per_match']]
                    scored.append((percentile(nets, 0.5), name))
                scored.sort(key=lambda item: (-item[0], item[1]))
                best_net, best_name = scored[0]
                if best_net <= 0:
                    recommendation = {'plan': '现状', 'note': '备选方案的净收益增量中位数均不为正'}
                else:
                    caveats = []
                    if validation:
                        noise = validation['mean_abs_error']['gross_cents'] or 0
                        unserved_error = validation['mean_abs_error']['unserved_share'] or 0
                        if len(scored) > 1 and best_net - scored[1][0] < noise:
                            caveats.append('方案间净收益差异小于基线回放误差，谨慎解读')
                        if unserved_error > 0.1:
                            caveats.append('基线回放对未服务比例的还原误差超过 0.1，结论需复核')
                    recommendation = {
                        'plan': best_name,
                        'median_net_increment_cents': round(best_net, 1),
                        'caveats': caveats,
                    }

        report = {
            'config': config,
            'matches': [m['date'] for m in pool_matches],
            'insufficient': insufficient,
            'feasibility': feasibility,
            'plans': plans_section,
            'validation': validation,
            'assumptions': assumption_notes(config),
            'recommendation': recommendation,
            'recommendation_blocked': blocked or None,
        }

        snapshot = []
        seen = set()
        referenced = [(shift.get('worker'), 'worker') for plan in candidates for shift in plan['shifts']]
        referenced += [(batch.get('ingredient'), 'ingredient') for plan in candidates for batch in plan['preparation']]
        referenced += [(name, 'worker') for name in constraints.get('immovable', [])]
        for key, kind in referenced:
            if not key or (kind, key) in seen:
                continue
            seen.add((kind, key))
            source = workers if kind == 'worker' else ingredients
            snapshot.append([kind, key, source[key]['revision'] if key in source else None])

        self.store.put('evaluation', id, {
            'created_at': datetime.now(timezone.utc).isoformat(),
            'plans': candidates,
            'constraints': constraints,
            'snapshot': snapshot,
            'report': report,
        }, create=True)
        return {'id': id, 'report': report}
