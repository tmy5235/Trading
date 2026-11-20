import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.market_history import available, save_history
from backend.observations import connect, record
from backend.live_history import snapshot

class MarketHistoryTests(unittest.TestCase):
    def test_history_revisions_and_stream_provenance(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'market.sqlite3'
            with closing(connect(path)) as db:
                candle={'timestamp':'2026-11-17T14:30:00Z','open':100,'high':103,'low':99,'close':102,'volume':1200}
                revision={**candle,'high':104,'close':103,'volume':1300}
                # A forming minute is never installed as completed history.
                forming={**candle,'timestamp':'2026-11-17T14:32:00Z'}
                self.assertEqual(save_history(db,'AAPL',[candle,forming],'2026-11-17T14:32:05+00:00'),1)
                self.assertEqual(save_history(db,'AAPL',[candle],'2026-11-17T14:32:10+00:00'),0)
                self.assertEqual(save_history(db,'AAPL',[revision],'2026-11-17T14:33:00+00:00'),1)
                early=available(db,'AAPL',datetime(2026,11,17,14,32,15,tzinfo=timezone.utc))
                later=available(db,'AAPL',datetime(2026,11,17,14,33,1,tzinfo=timezone.utc))
                self.assertEqual((early[0]['close'], early[0]['volume']),(102,1200))
                self.assertEqual((later[0]['close'], later[0]['volume']),(103,1300))
                t=datetime(2026,11,17,14,32,5,tzinfo=timezone.utc)
                record(db,{'id':'AAPL','time':str(int(t.timestamp()*1000)),'price':105,'day_volume':'2000'},t.isoformat())
                self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],1)
                with patch('backend.live_history.connect',lambda:connect(path)):
                    result=snapshot('AAPL','1m',datetime(2026,11,17,14,32,30,tzinfo=timezone.utc))
                    self.assertEqual([r['data_source'] for r in result['records']],['Yahoo history','Yahoo stream'])
                    self.assertEqual([r['candle']['close'] for r in result['records']],[102,105])
                    self.assertEqual([r['ended'] for r in result['records']],[True,False])
                    five=snapshot('AAPL','5m',datetime(2026,11,17,14,32,30,tzinfo=timezone.utc))
                    self.assertEqual(five['records'][0]['data_source'],'Yahoo history + stream')
                    self.assertEqual(five['records'][0]['candle']['high'],105)
                    self.assertEqual(five['records'][0]['coverage']['minutes_present'],2)

if __name__=='__main__': unittest.main()
