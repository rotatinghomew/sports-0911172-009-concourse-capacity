from .store import require, money

from .staffing import Staffing
from .kitchen import Kitchen

class Scenarios:
    def __init__(self,store):
        self.store=store

    def save(self,id,shifts,preparation,reason):
        require(bool(reason),'情景说明为空')
        return self.store.put('scenario',id,dict(shifts=shifts,preparation=preparation,
            reason=reason,state='draft'),create=True)

    def adopt(self,id):
        with self.store.atomic():
            record=self.store.get('scenario',id)
            if record['state']=='adopted':
                return record
            for index,shift in enumerate(record['shifts']):
                Staffing(self.store).assign(id=f'{id}:{index}',**shift)
            for index,batch in enumerate(record['preparation']):
                Kitchen(self.store).prepare(id=f'{id}:{index}',**batch)
            record['state']='adopted'
            return self.store.put('scenario',id,record)
