from .store import require, money

class Queueing:
    def __init__(self,store):
        self.store=store

    def issue(self,id,party,at,recorded_at,window):
        return self.store.put('queue_ticket',id,dict(party=party,at=at,recorded_at=recorded_at,
            window=window,state='waiting'),create=True)

    def call(self,id,at):
        record=self.store.get('queue_ticket',id)
        require(record['state']=='waiting','队列状态无效')
        require(record['at'] is None or at>=record['at'],'叫号早于取号')
        record.update(state='called',called_at=at)
        return self.store.put('queue_ticket',id,record)

    def abandon(self,id,at):
        record=self.store.get('queue_ticket',id)
        require(record['state']=='waiting','只能放弃待叫号')
        require(record['at'] is None or at>=record['at'],'离队早于取号')
        record.update(state='abandoned',abandoned_at=at)
        return self.store.put('queue_ticket',id,record)

    def complete(self,id,at,cents):
        record=self.store.get('queue_ticket',id)
        require(record['state']=='called' and at>=record['called_at'],'完成时间或状态无效')
        record.update(state='served',completed_at=at,cents=money(cents))
        return self.store.put('queue_ticket',id,record)
