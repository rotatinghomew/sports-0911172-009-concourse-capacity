from .store import require, money

class Staffing:
    def __init__(self,store):
        self.store=store

    def worker(self,id,skills):
        return self.store.put('worker',id,dict(skills=skills,absent=False),create=True)

    def absence(self,id):
        record=self.store.get('worker',id)
        record['absent']=True
        return self.store.put('worker',id,record)

    def assign(self,id,worker,window,skill,start,end):
        record=self.store.get('worker',worker)
        require(start<end and not record['absent'] and skill in record['skills'],'无法安排该技能班次')
        require(not any(item['worker']==worker and start<item['end'] and end>item['start']
            for item in self.store.all('shift')),'员工时间冲突')
        return self.store.put('shift',id,dict(worker=worker,window=window,skill=skill,
            start=start,end=end),create=True)
