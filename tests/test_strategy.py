import unittest
from backend.strategy import STRATEGY_ID, expected_action
from backend.decisions import evaluate


class StrategyTests(unittest.TestCase):
    def test_policy_and_independent_validation(self):
        base={'strategy_id':STRATEGY_ID,'candle':{'close':100},'sma':{'short':105,'long':100,'signal':'up'},'account':{'cash':'1000','shares':0,'other_open_positions':0},'order_size':1}
        for shares,signal,action in [(0,'up','buy'),(0,'down','wait'),(0,None,'wait'),(4,'up','hold'),(4,'down','close'),(4,None,'hold')]:
            snapshot={**base,'sma':{**base['sma'],'signal':signal},'account':{**base['account'],'shares':shares}}
            self.assertEqual(expected_action(snapshot),action)
            self.assertEqual(evaluate(snapshot,action)[0],'valid')
        self.assertEqual(evaluate(base,'wait')[0],'invalid_proposal')
        self.assertEqual(evaluate({**base,'account':{**base['account'],'cash':'10'}},'buy')[0],'invalid_proposal')


if __name__=='__main__': unittest.main()
