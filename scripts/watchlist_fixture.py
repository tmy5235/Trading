from scripts.classroom_fixture import app
from backend import watchlist
from tests.watchlist_helpers import fake_run

watchlist.Runner.run = fake_run
