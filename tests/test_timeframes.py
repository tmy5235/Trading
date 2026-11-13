import unittest
from datetime import datetime, timedelta, timezone
from backend.timeframes import aggregate


def dt(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def row(ts, o, h, l, c, v, available=None):
    return {'candle': {'timestamp': ts, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v},
            'available_at': dt(available) if available else dt(ts) + timedelta(minutes=1)}


class TimeframeTests(unittest.TestCase):
    def test_sample_sessions_coverage_revisions_and_replay(self):
        sessions = [(dt('2026-11-09T14:30:00Z'), dt('2026-11-09T21:00:00Z')),
                    (dt('2026-11-10T14:30:00Z'), dt('2026-11-10T21:00:00Z'))]
        data = [row('2026-11-09T14:30:00Z',100,103,99,102,1200),
                row('2026-11-09T14:31:00Z',102,104,100,101,850),
                row('2026-11-09T14:32:00Z',101,105,101,104,1500)]
        now = dt('2026-11-09T14:34:00Z')
        result = aggregate(data, sessions, '5m', now)[0]
        self.assertEqual(result['candle'], {'timestamp':'2026-11-09T14:30:00Z','open':100,'high':105,'low':99,'close':104,'volume':3550})
        self.assertEqual(result['coverage'], {'minutes_present':3,'minutes_expected':5,'complete':False})
        self.assertFalse(result['ended'])
        daily = aggregate(data, sessions, '1d', now)[0]
        self.assertEqual(daily['coverage']['minutes_present'], 3)
        self.assertFalse(daily['coverage']['complete'])
        data.extend([row('2026-11-09T14:35:00Z',104,106,103,105,50), row('2026-11-10T14:30:00Z',105,107,104,106,70)])
        later = dt('2026-11-10T14:32:00Z')
        five = aggregate(data, sessions, '5m', later)
        self.assertEqual([x['candle']['timestamp'] for x in five], ['2026-11-09T14:30:00Z','2026-11-09T14:35:00Z','2026-11-10T14:30:00Z'])
        days = aggregate(data, sessions, '1d', later)
        self.assertEqual([x['coverage']['minutes_present'] for x in days], [4,1])
        revised = row('2026-11-09T14:31:00Z',102,110,100,109,None,'2026-11-09T14:40:00Z')
        data.append(revised)
        self.assertEqual(aggregate(data, sessions, '5m', now)[0]['candle']['high'],105)
        after = aggregate(data, sessions, '5m', later)[0]
        self.assertEqual(after['candle']['high'],110)
        self.assertIsNone(after['candle']['volume'])
        self.assertFalse(after['coverage']['complete'])
        self.assertEqual(len(aggregate(data, sessions, '5m', dt('2026-11-09T14:31:00Z'))),1)
        self.assertEqual(aggregate(data, sessions, '5m', dt('2026-11-09T14:31:00Z'))[0]['coverage']['minutes_present'],1)


class ExchangeCalendarTests(unittest.TestCase):
    def test_daylight_saving_and_early_close(self):
        import exchange_calendars as xcals
        calendar = xcals.get_calendar('XNAS', start='2026-01-01', end='2026-12-31')
        schedule = calendar.schedule
        self.assertEqual(schedule.loc['2026-03-06', 'open'].isoformat(), '2026-03-06T14:30:00+00:00')
        self.assertEqual(schedule.loc['2026-03-09', 'open'].isoformat(), '2026-03-09T13:30:00+00:00')
        early = schedule.loc['2026-11-27']
        self.assertEqual(early['close'].isoformat(), '2026-11-27T18:00:00+00:00')
        sessions = [(early['open'].to_pydatetime(), early['close'].to_pydatetime())]
        minutes = [row('2026-11-27T17:59:00Z', 1, 1, 1, 1, 0),
                   row('2026-11-27T18:00:00Z', 2, 2, 2, 2, 1)]
        result = aggregate(minutes, sessions, '1h', dt('2026-11-27T18:01:00Z'))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['candle']['timestamp'], '2026-11-27T17:30:00Z')
        self.assertEqual(result[0]['candle']['volume'], 0)
        self.assertEqual(result[0]['coverage']['minutes_expected'], 30)
        self.assertTrue(result[0]['ended'])


if __name__ == '__main__':
    unittest.main()
