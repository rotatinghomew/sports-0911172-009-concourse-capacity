from .store import require, money

class Kitchen:
    def __init__(self,store):
        self.store=store

    def stock(self,id,units):
        require(units>=0,'食材量无效')
        return self.store.put('ingredient',id,dict(units=units),create=True)

    def prepare(self,id,ingredient,units,servings,at):
        with self.store.atomic():
            record=self.store.get('ingredient',ingredient)
            require(0<units<=record['units'] and servings>0,'备餐量无效')
            result=self.store.put('prepared',id,dict(ingredient=ingredient,units=units,
                servings=servings,remaining=servings,at=at),create=True)
            record['units']-=units
            self.store.put('ingredient',ingredient,record)
            return result

    def consume(self,id,count):
        record=self.store.get('prepared',id)
        require(0<count<=record['remaining'],'备餐不足')
        record['remaining']-=count
        return self.store.put('prepared',id,record)

    def waste(self,id,units):
        record=self.store.get('ingredient',id)
        require(0<units<=record['units'],'报损量无效')
        record['units']-=units
        return self.store.put('ingredient',id,record)
