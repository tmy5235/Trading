import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.market_history import save_history
from backend.observations import connect, record
from backend.paper import initialize, execute_action, account
from backend.replay import chart_snapshot, options


class ChartSnapshotTests(unittest.TestCase):
    def test_cutoff_history_revision_timeframes_and_read_only_account(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'snapshot.sqlite3'
            with closing(connect(path)) as db:
                initialize(db)
                original={'timestamp':'2026-11-09T14:30:00Z','open':100,'high':103,'low':99,'close':102,'volume':1200}
                save_history(db,'AAPL',[original],'2026-11-09T14:32:10Z')
                save_history(db,'AAPL',[{**original,'high':105,'close':104}],'2026-11-09T14:34:00Z')
                for timestamp, price, receipt in [('2026-11-09T14:31:05+00:00',110,'2026-11-09T14:31:06Z'),
                                                  ('2026-11-09T14:31:50+00:00',105,'2026-11-09T14:31:51Z'),
                                                  ('2026-11-09T14:32:05+00:00',115,'2026-11-09T14:33:00Z')]:
                    t=datetime.fromisoformat(timestamp)
                    record(db,{'id':'AAPL','time':str(int(t.timestamp()*1000)),'price':price},receipt)
                execute_action(db,request_id='test-pending',symbol='AAPL',action='buy',quantity=1,mode='replay',scope='test',cursor=0)
                counts=[db.execute('select count(*) from '+table).fetchone()[0] for table in ('observations','receipts','yahoo_minute_bars','paper_fills')]
            before=account(path)
            kwargs={'symbol':'AAPL','start':'2026-11-09T14:30:00Z','end':'2026-11-09T14:32:40Z','path':path}
            one=chart_snapshot(timeframe='1m',**kwargs)
            self.assertEqual([r['candle']['timestamp'] for r in one['records']],['2026-11-09T14:30:00Z','2026-11-09T14:31:00Z'])
            self.assertEqual([r['candle']['close'] for r in one['records']],[102,105])
            self.assertEqual([r['data_source'] for r in one['records']],['Yahoo history','Yahoo stream'])
            five=chart_snapshot(timeframe='5m',**kwargs)
            self.assertEqual(len(five['records']),1)
            self.assertEqual({k:five['records'][0]['candle'][k] for k in ('open','high','low','close')},{'open':100,'high':110,'low':99,'close':105})
            self.assertFalse(five['records'][0]['ended'])
            revised=chart_snapshot('AAPL',kwargs['start'],'2026-11-09T14:34:01Z','1m',path=path)
            self.assertEqual([r['candle']['close'] for r in revised['records']],[104,105,115])
            self.assertEqual(chart_snapshot('AAPL','2026-11-10T14:30:00Z','2026-11-10T15:00:00Z','1m',path=path)['records'],[])
            self.assertEqual(account(path),before)
            with closing(connect(path)) as db:
                self.assertEqual(counts,[db.execute('select count(*) from '+table).fetchone()[0] for table in ('observations','receipts','yahoo_minute_bars','paper_fills')])
            self.assertEqual(options(path)[0]['symbol'],'AAPL')


if __name__=='__main__': unittest.main()
