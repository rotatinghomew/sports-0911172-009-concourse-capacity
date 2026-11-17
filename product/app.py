import argparse
import json
import sys
from pathlib import Path
from .store import Store
from .queueing import Queueing
from .kitchen import Kitchen
from .staffing import Staffing
from .scenarios import Scenarios


class Application:
    def __init__(self, path=':memory:'):
        self.store=Store(path)
        self.services={'queueing':Queueing(self.store),'kitchen':Kitchen(self.store),'staffing':Staffing(self.store),'scenarios':Scenarios(self.store)}

    def execute(self, command, arguments):
        service, separator, operation=command.partition('.')
        if not separator or operation.startswith('_') or service not in self.services:
            raise ValueError('未知业务操作')
        handler=getattr(self.services[service],operation,None)
        if not callable(handler):
            raise ValueError('未知业务操作')
        with self.store.atomic():
            return handler(**arguments)

    def replay(self, operations):
        return [self.execute(command,arguments) for command,arguments in operations]


def main():
    parser=argparse.ArgumentParser(description='场馆餐饮队列的流失校正与排班情景评估本地操作台')
    parser.add_argument('action',choices=['demo','replay','inspect'])
    parser.add_argument('--db',default=':memory:')
    parser.add_argument('--file',type=Path)
    args=parser.parse_args()
    app=Application(args.db)
    try:
        if args.action=='demo':
            path=Path(__file__).parent.parent/'data'/'demo.json'
            results=app.replay(json.loads(path.read_text()))
        elif args.action=='replay':
            if args.file is None:
                parser.error('replay 需要 --file')
            results=app.replay(json.loads(args.file.read_text()))
        else:
            results=[]
        print(json.dumps(dict(results=results,state=app.store.snapshot()),ensure_ascii=False,indent=2))
    except (ValueError,TypeError,KeyError) as error:
        print(str(error),file=sys.stderr)
        return 2
    finally:
        app.store.close()
    return 0
