from .store import require, money

from .staffing import Staffing
from .kitchen import Kitchen

class Scenarios:
    def __init__(self,store):
        self.store=store

    def save(self,id,shifts,preparation,reason,evaluation=None):
        require(bool(reason),'情景说明为空')
        record=dict(shifts=shifts,preparation=preparation,reason=reason,state='draft')
        if evaluation is not None:
            self.store.get('evaluation',evaluation)
            record['evaluation']=evaluation
        return self.store.put('scenario',id,record,create=True)

    def adopt(self,id):
        with self.store.atomic():
            record=self.store.get('scenario',id)
            if record['state']=='adopted':
                return record
            for index,shift in enumerate(record['shifts']):
                Staffing(self.store).assign(id=f'{id}:{index}',**shift)
            for index,batch in enumerate(record['preparation']):
                Kitchen(self.store).prepare(id=f'{id}:{index}',**batch)
            changes=self.input_changes(record)
            if changes:
                record['input_changes']=changes
            record['state']='adopted'
            return self.store.put('scenario',id,record)

    def input_changes(self,record):
        """分析存档之后发生变化的人员与食材；采纳时如实标注，不静默沿用旧建议。"""
        evaluation=record.get('evaluation')
        if not evaluation:
            return []
        try:
            archived=self.store.get('evaluation',evaluation)
        except ValueError:
            return []
        changes=[]
        for kind,key,revision in archived.get('snapshot',[]):
            try:
                current=self.store.get(kind,key)
            except ValueError:
                changes.append(f'{kind}/{key} 已不存在')
                continue
            if current['revision']!=revision:
                changes.append(f"{kind}/{key} 修订 {revision}→{current['revision']}")
        return changes
