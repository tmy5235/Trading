import asyncio
import unittest
from unittest.mock import patch
from backend import watchlist
from backend.chart_decisions import decode_png
from tests.watchlist_helpers import fake_run

class EvidenceTests(unittest.TestCase):
    def test_frozen_images_and_cutoffs(self):
        rows, captures=watchlist.capture_cycle(0)
        before=captures[rows[0]['snapshot_id']]['image_url']
        watchlist.capture_cycle(1)
        self.assertEqual(before,watchlist.capture_cycle(0)[1][rows[0]['snapshot_id']]['image_url'])
        self.assertTrue(decode_png(before).startswith(b'\x89PNG'))
        self.assertEqual([r['status'] for r in rows],['ready','ready','ready','stale','missing'])
        self.assertEqual([b['time'] for b in rows[0]['bars']],['2026-11-16T14:40:00Z','2026-11-16T14:41:00Z'])

    def test_duplicate_wrong_ticker_and_threshold(self):
        rows,_=watchlist.capture_cycle(0); sid=rows[0]['snapshot_id']
        entries=[watchlist.Candidate(ticker='AAPL',snapshot_id=sid,observation='rise'),watchlist.Candidate(ticker='AAPL',snapshot_id=sid,observation='again'),watchlist.Candidate(ticker='MSFT',snapshot_id=sid,observation='wrong')]
        accepted,errors=watchlist.validate_candidates(watchlist.Shortlist(candidates=entries),rows,0)
        self.assertEqual(len(accepted),1); self.assertEqual(len(errors),2)
        accepted,errors=watchlist.validate_candidates(watchlist.Shortlist(candidates=entries[:1]),rows,0.5)
        self.assertEqual(len(accepted),0); self.assertEqual(len(errors),1)
        accepted,errors=watchlist.validate_candidates(watchlist.Shortlist(candidates=[watchlist.Candidate(ticker='MSFT',snapshot_id=sid,observation='wrong')]),rows,0)
        self.assertFalse(accepted); self.assertTrue(errors)

class RunTests(unittest.IsolatedAsyncioTestCase):
    async def test_limit_and_late_result(self):
        def result(image,context,**kwargs):
            return {'status':'ok','context':context,'answer':{'choice':'wait','probability':0.0,'probabilities':{'wait':0.0}}}
        with patch.dict('os.environ',{'OPENAI_API_KEY':'fixture'}),patch.object(watchlist.Runner,'run',fake_run),patch.object(watchlist.classroom,'decide',side_effect=result):
            session=watchlist.Session(watchlist.Settings(call_limit=1),True)
            await watchlist.run_session(session)
            self.assertEqual(session.calls,1)
            self.assertTrue(any(e['kind']=='limit' for e in session.events))
            self.assertFalse(session.cycles[0]['assessments'])
            session=watchlist.Session(watchlist.Settings(),True)
            def late(image,context,**kwargs):
                session.stopped=True
                return result(image,context,**kwargs)
            with patch.object(watchlist.classroom,'decide',side_effect=late): await watchlist.run_session(session)
            self.assertTrue(any(e['kind']=='late' for e in session.events))
            self.assertFalse(session.cycles[0]['assessments'])

if __name__=='__main__': unittest.main()
