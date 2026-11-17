import random
import json
from datetime import date, timedelta

def generate():
    rng = random.Random(117209)
    operations = []

    def add(command, **arguments):
        operations.append([command, arguments])

    def day(offset):
        return (date(2026, 7, 1) + timedelta(days=offset)).isoformat()
    for index in range(8):
        add('staffing.worker', id=f'w{index}', skills=['cold', 'hot'] if index % 3 == 0 else ['cold'])
    add('kitchen.stock', id='rice', units=10000)
    for match in range(24):
        base = day(match * 3)
        add('kitchen.prepare', id=f'prep{match}', ingredient='rice', units=100, servings=80, at=f'{base}T19:30:00')
        for index in range(40):
            key = f'q{match}:{index}'
            minute = 40 + index % 15
            at = f'{base}T19:{minute:02d}:00'
            add('queueing.issue', id=key, party=f'g{index // 3}', at=None if index % 13 == 0 else at, recorded_at=f'{base}T19:59:00' if index % 13 == 0 else at, window=f'v{index % 3}')
            if index % 7 == 0:
                continue
            if rng.random() < 0.22:
                add('queueing.abandon', id=key, at=f'{base}T19:{min(59, minute + 4):02d}:00')
            else:
                add('queueing.call', id=key, at=f'{base}T19:{min(59, minute + 3 + index % 4):02d}:00')
                add('queueing.complete', id=key, at=f'{base}T20:00:00', cents=2000 + index % 4 * 1000)
                add('kitchen.consume', id=f'prep{match}', count=1)
        add('staffing.assign', id=f'shift{match}', worker=f'w{match % 8}', window='v0', skill='cold', start=f'{base}T19:30:00', end=f'{base}T20:15:00')
    return operations

def main():
    import argparse
    parser = argparse.ArgumentParser(description='生成本地合成业务记录，不包含真实个人资料')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    from pathlib import Path
    Path(args.output).write_text(json.dumps(generate(), ensure_ascii=False, indent=2) + '\n')

if __name__=='__main__':
    main()
